use std::{
    ffi::{c_char, c_int, c_void, CStr, CString},
    path::Path,
    ptr,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
};
type P = *mut c_void;
type S = c_int;

macro_rules! api {
    ($($name:ident($($arg:ident:$ty:ty),*) -> $ret:ty;)+) => {
        #[cfg(windows)] extern "C" { $(fn $name($($arg:$ty),*) -> $ret;)+ }
        pub struct Api {
            #[cfg(not(windows))] _library: libloading::Library,
            $(pub $name: unsafe extern "C" fn($($ty),*) -> $ret,)+
        }
        impl Api {
            pub unsafe fn open(path: Option<&Path>) -> Result<Arc<Self>,String> {
                #[cfg(not(windows))] let lib = libloading::Library::new(path.ok_or("library is required on macOS")?.canonicalize().map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
                #[cfg(windows)] if path.is_some() { return Err("Windows uses the linked CPU engine".into()); }
                Ok(Arc::new(Self {
                    $( $name: { #[cfg(windows)] { $name } #[cfg(not(windows))] { *lib.get(concat!(stringify!($name),"\0").as_bytes()).map_err(|e|e.to_string())? } },)+
                    #[cfg(not(windows))] _library: lib,
                }))
            }
        }
    }
}
api! {
 speech_version() -> *const c_char;
 speech_last_error() -> *const c_char;
 speech_api_version_major() -> c_int;
 speech_load_params_new(out:*mut P) -> S;
 speech_load_params_free(p:P) -> ();
 speech_load_params_set_device(p:P,v:*const c_char) -> S;
 speech_load_params_set_threads(p:P,v:c_int) -> S;
 speech_load_params_set_warmup(p:P,v:c_int) -> S;
 speech_model_load(path:*const c_char,params:P,out:*mut P) -> S;
 speech_model_free(p:P) -> ();
 speech_model_get_info(p:P,out:*mut P) -> S;
 speech_model_info_device(p:P) -> *const c_char;
 speech_model_info_name(p:P) -> *const c_char;
 speech_model_info_free(p:P) -> ();
 speech_option_from_name(name:*const c_char,out:*mut c_int) -> S;
 speech_request_new(model:P,out:*mut P) -> S;
 speech_request_free(p:P) -> ();
 speech_request_set_audio(p:P,a:*const f32,n:usize,rate:c_int) -> S;
 speech_request_set_string(p:P,opt:c_int,v:*const c_char) -> S;
 speech_request_set_float(p:P,opt:c_int,v:f64) -> S;
 speech_request_set_bool(p:P,opt:c_int,v:c_int) -> S;
 speech_request_cancel(p:P) -> ();
 speech_transcribe(p:P) -> S;
 speech_request_result(p:P) -> P;
 speech_result_text(p:P) -> *const c_char;
}
fn cstr(p: *const c_char) -> String {
    if p.is_null() {
        String::new()
    } else {
        unsafe { CStr::from_ptr(p).to_string_lossy().into_owned() }
    }
}
impl Api {
    fn check(&self, s: S) -> Result<(), String> {
        if s == 0 {
            Ok(())
        } else {
            Err(format!(
                "native status {s}: {}",
                cstr(unsafe { (self.speech_last_error)() })
            ))
        }
    }
    fn option(&self, name: &str) -> Result<c_int, String> {
        let n = CString::new(name).unwrap();
        let mut o = 0;
        self.check(unsafe { (self.speech_option_from_name)(n.as_ptr(), &mut o) })?;
        Ok(o)
    }
}

pub struct Active {
    api: Arc<Api>,
    request: Mutex<usize>,
}
impl Active {
    pub fn new(api: Arc<Api>) -> Arc<Self> {
        Arc::new(Self {
            api,
            request: Mutex::new(0),
        })
    }
    pub fn cancel(&self) {
        let p = self.request.lock().unwrap();
        if *p != 0 {
            unsafe { (self.api.speech_request_cancel)(*p as P) }
        }
    }
}
struct Handle<'a> {
    ptr: P,
    free: unsafe extern "C" fn(P),
    _api: &'a Api,
}
// Declared after the request handle so cancellation loses access before the
// C request is freed, including during an early return or panic unwinding.
struct InFlight<'a>(&'a Active);
impl Drop for InFlight<'_> {
    fn drop(&mut self) {
        *self.0.request.lock().unwrap() = 0;
    }
}
impl Drop for Handle<'_> {
    fn drop(&mut self) {
        unsafe { (self.free)(self.ptr) }
    }
}
pub struct Model {
    api: Arc<Api>,
    ptr: P,
    pub active: Arc<Active>,
}
impl Model {
    pub fn load(api: Arc<Api>, path: &Path, threads: i32) -> Result<Self, String> {
        if unsafe { (api.speech_api_version_major)() } != 4
            || cstr(unsafe { (api.speech_version)() }) != "0.10.0"
        {
            return Err("wrong speech API".into());
        }
        let mut params = ptr::null_mut();
        api.check(unsafe { (api.speech_load_params_new)(&mut params) })?;
        let params = Handle {
            ptr: params,
            free: api.speech_load_params_free,
            _api: &api,
        };
        api.check(unsafe {
            (api.speech_load_params_set_device)(params.ptr, b"cpu\0".as_ptr().cast())
        })?;
        api.check(unsafe { (api.speech_load_params_set_threads)(params.ptr, threads) })?;
        api.check(unsafe { (api.speech_load_params_set_warmup)(params.ptr, 1) })?;
        let path = CString::new(path.to_str().ok_or("non-unicode model path")?)
            .map_err(|e| e.to_string())?;
        let mut model = ptr::null_mut();
        api.check(unsafe { (api.speech_model_load)(path.as_ptr(), params.ptr, &mut model) })?;
        let mut info = ptr::null_mut();
        if let Err(e) = api.check(unsafe { (api.speech_model_get_info)(model, &mut info) }) {
            unsafe { (api.speech_model_free)(model) };
            return Err(e);
        }
        let cpu = cstr(unsafe { (api.speech_model_info_device)(info) }) == "CPU";
        let haas = cstr(unsafe { (api.speech_model_info_name)(info) }) == "Haas-ja-v1.0";
        unsafe { (api.speech_model_info_free)(info) };
        if !cpu || !haas {
            unsafe { (api.speech_model_free)(model) };
            return Err("wrong model or device".into());
        }
        drop(params);
        let active = Active::new(api.clone());
        Ok(Self {
            api,
            ptr: model,
            active,
        })
    }
    pub fn decode(
        &mut self,
        audio: &[f32],
        keywords: &str,
        cancel: &AtomicBool,
    ) -> Result<String, String> {
        if audio.is_empty() || audio.len() > 12 * 16000 || audio.iter().any(|x| !x.is_finite()) {
            return Err("invalid audio".into());
        }
        let mut request = ptr::null_mut();
        self.api
            .check(unsafe { (self.api.speech_request_new)(self.ptr, &mut request) })?;
        let request = Handle {
            ptr: request,
            free: self.api.speech_request_free,
            _api: &self.api,
        };
        self.api.check(unsafe {
            (self.api.speech_request_set_audio)(request.ptr, audio.as_ptr(), audio.len(), 16000)
        })?;
        if !keywords.is_empty() {
            let text = CString::new(keywords).map_err(|e| e.to_string())?;
            self.api.check(unsafe {
                (self.api.speech_request_set_string)(
                    request.ptr,
                    self.api.option("keywords")?,
                    text.as_ptr(),
                )
            })?;
            self.api.check(unsafe {
                (self.api.speech_request_set_float)(
                    request.ptr,
                    self.api.option("keywords_alpha")?,
                    1.0,
                )
            })?;
            self.api.check(unsafe {
                (self.api.speech_request_set_bool)(
                    request.ptr,
                    self.api.option("keywords_guard")?,
                    1,
                )
            })?;
        }
        *self.active.request.lock().unwrap() = request.ptr as usize;
        let in_flight = InFlight(&self.active);
        if cancel.load(Ordering::Acquire) {
            self.active.cancel();
        }
        let status = unsafe { (self.api.speech_transcribe)(request.ptr) };
        drop(in_flight);
        if cancel.load(Ordering::Acquire) || status == 1 {
            return Err("cancelled".into());
        }
        self.api.check(status)?;
        let result = unsafe { (self.api.speech_request_result)(request.ptr) };
        Ok(cstr(unsafe { (self.api.speech_result_text)(result) })
            .trim()
            .to_owned())
    }
}
impl Drop for Model {
    fn drop(&mut self) {
        unsafe { (self.api.speech_model_free)(self.ptr) }
    }
}
