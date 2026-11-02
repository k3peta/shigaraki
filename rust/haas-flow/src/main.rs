mod api;
mod segments;
use api::{Active, Api, DecodeError, Model};
use base64::{engine::general_purpose::STANDARD, Engine};
use segments::{Segment, Segments, FRAME, RATE};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    io::{self, BufRead, Read, Write},
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Condvar, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
const FIRST_SAMPLES: usize = if cfg!(feature = "compat-timing") {
    8800
} else {
    5600
};
// Earlier first text, without increasing repeated full-window CPU inference.
const INTERVAL_MS: u64 = 650;
// Qt stops recording after 300 seconds. Allow its final polling/audio callback
// tail without turning the normal automatic stop into a fatal protocol error.
const MAX_SAMPLES: usize = 305 * RATE;
fn audio_fits(existing: usize, bytes: usize) -> bool {
    bytes > 0 && bytes % 2 == 0 && bytes <= 32000 && existing + bytes / 2 <= MAX_SAMPLES
}

#[derive(Deserialize, Debug)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum Input {
    Start {
        id: u64,
        #[serde(default)]
        keywords: String,
        #[serde(default = "yes")]
        preview: bool,
    },
    Audio {
        id: u64,
        seq: u64,
        pcm: String,
    },
    Stop {
        id: u64,
    },
    Cancel {
        id: u64,
    },
}
fn yes() -> bool {
    true
}
#[derive(Default, Clone, Serialize)]
struct Metrics {
    decode_count: u64,
    decode_seconds: f64,
    last_decode_seconds: f64,
    max_decode_seconds: f64,
    last_audio_seconds: f64,
    processed_audio_seconds: f64,
    max_window_seconds: f64,
    max_backlog_seconds: f64,
    pending_segments_max: usize,
    preempted_decodes: u64,
    preempted_decode_seconds: f64,
    cache_hits: u64,
}
struct Session {
    id: u64,
    audio: Vec<f32>,
    seq: u64,
    stop: bool,
    stop_signal: Arc<AtomicBool>,
    sealed: bool,
    preview: bool,
    cancel: Arc<AtomicBool>,
    segments: Segments,
    committed: String,
    draft_start: Option<usize>,
    draft: String,
    cache: Option<(usize, usize, String)>,
    last_decode: Option<Instant>,
    metrics: Metrics,
    keywords: String,
    started: Instant,
}
impl Session {
    fn new(id: u64, keywords: String) -> Self {
        Self {
            id,
            audio: Vec::new(),
            seq: 0,
            stop: false,
            stop_signal: Arc::new(AtomicBool::new(false)),
            sealed: false,
            preview: true,
            cancel: Arc::new(AtomicBool::new(false)),
            segments: Segments::default(),
            committed: String::new(),
            draft_start: None,
            draft: String::new(),
            cache: None,
            last_decode: None,
            metrics: Metrics::default(),
            keywords,
            started: Instant::now(),
        }
    }
}
#[derive(Default)]
struct State {
    session: Option<Session>,
    last_id: u64,
    quit: bool,
    error: Option<String>,
}
type Shared = Arc<(Mutex<State>, Condvar)>;
fn output(value: Value) -> io::Result<()> {
    let stdout = io::stdout();
    let mut out = stdout.lock();
    serde_json::to_writer(&mut out, &value)?;
    out.write_all(b"\n")?;
    out.flush()
}
fn apply(state: &mut State, input: Input, active: &Active) -> Result<(), String> {
    match input {
        Input::Start {
            id,
            keywords,
            preview,
        } => {
            if state.session.is_some() || id <= state.last_id || id == 0 {
                return Err("invalid or busy session".into());
            }
            if keywords.len() > 2400 || keywords.contains('\0') {
                return Err("invalid keywords".into());
            }
            state.last_id = id;
            let mut session = Session::new(id, keywords);
            session.preview = preview;
            state.session = Some(session);
        }
        Input::Audio { id, seq, pcm } => {
            let Some(s) = state.session.as_mut().filter(|s| s.id == id) else {
                return if id <= state.last_id {
                    Ok(())
                } else {
                    Err("unknown session".into())
                };
            };
            if s.cancel.load(Ordering::Acquire) {
                return Ok(());
            }
            if s.stop || seq != s.seq {
                return Err("audio sequence mismatch".into());
            }
            if pcm.len() > 44_000 {
                return Err("audio message too large".into());
            }
            let bytes = STANDARD.decode(pcm).map_err(|_| "invalid PCM base64")?;
            if !audio_fits(s.audio.len(), bytes.len()) {
                return Err("invalid audio size".into());
            }
            s.seq += 1;
            s.audio.extend(
                bytes
                    .chunks_exact(2)
                    .map(|v| i16::from_le_bytes([v[0], v[1]]) as f32 / 32768.),
            );
        }
        Input::Stop { id } => {
            if let Some(s) = state.session.as_mut().filter(|s| s.id == id) {
                s.stop = true;
                s.stop_signal.store(true, Ordering::Release);
                active.cancel_preview();
            }
        }
        Input::Cancel { id } => {
            if let Some(s) = state.session.as_mut().filter(|s| s.id == id) {
                s.cancel.store(true, Ordering::Release);
                active.cancel();
            }
        }
    }
    Ok(())
}
fn input_thread(shared: Shared, active: Arc<Active>) {
    let stdin = io::stdin();
    let mut reader = stdin.lock();
    loop {
        let mut line = Vec::new();
        let read = (&mut reader).take(262145).read_until(b'\n', &mut line);
        let command = match read {
            Ok(0) => None,
            Ok(n) if n <= 262144 && line.last() == Some(&b'\n') => Some(
                serde_json::from_slice::<Input>(&line)
                    .map_err(|_| "invalid control message".to_string()),
            ),
            _ => Some(Err("invalid control framing".into())),
        };
        let (lock, wake) = &*shared;
        let mut state = lock.lock().unwrap();
        match command {
            None => state.quit = true,
            Some(Ok(input)) => {
                if let Err(e) = apply(&mut state, input, &active) {
                    state.error = Some(e);
                    state.quit = true;
                }
            }
            Some(Err(e)) => {
                state.error = Some(e);
                state.quit = true;
            }
        }
        if state.quit {
            if let Some(s) = &state.session {
                s.cancel.store(true, Ordering::Release);
            }
            active.cancel();
            wake.notify_all();
            return;
        }
        wake.notify_all();
    }
}
struct Plan {
    id: u64,
    segment: Segment,
    final_: bool,
    audio: Vec<f32>,
    keywords: String,
    cancel: Arc<AtomicBool>,
    stop_signal: Arc<AtomicBool>,
    cached: Option<String>,
}
fn covers_final_tail(audio: &[f32], cached_end: usize, final_end: usize) -> bool {
    cached_end >= final_end
        && cached_end - final_end <= RATE * 3 / 5
        && audio[final_end..cached_end].chunks(FRAME).all(|frame| {
            (frame.iter().map(|x| x * x).sum::<f32>() / frame.len() as f32).sqrt() < 0.004
        })
}
fn retry_preempted_preview(final_: bool, stop: bool, error: &DecodeError) -> bool {
    !final_ && stop && *error == DecodeError::Interrupted
}
fn plan(s: &mut Session) -> Option<Plan> {
    let available = if s.stop {
        s.audio.len()
    } else {
        s.audio.len() / FRAME * FRAME
    };
    if available > s.segments.cursor {
        s.segments.feed(&s.audio[s.segments.cursor..available]);
    }
    if s.stop && !s.sealed {
        s.segments.finish();
        s.sealed = true;
    }
    s.metrics.pending_segments_max = s.metrics.pending_segments_max.max(s.segments.pending.len());
    loop {
        let final_ = !s.segments.pending.is_empty();
        let segment = if final_ {
            s.segments.pending.front().copied()
        } else {
            s.segments.draft()
        }?;
        if segment.voiced < RATE / 10 {
            if final_ {
                s.segments.pending.pop_front();
                continue;
            } else {
                return None;
            }
        }
        let cached = s
            .cache
            .as_ref()
            .filter(|(a, b, _)| {
                *a == segment.start
                    && (*b == segment.end || final_ && covers_final_tail(&s.audio, *b, segment.end))
            })
            .map(|(_, _, t)| t.clone());
        if !final_
            && (!s.preview
                || segment.end - segment.start < FIRST_SAMPLES
                || cached.is_some()
                || s.last_decode
                    .is_some_and(|t| t.elapsed() < Duration::from_millis(INTERVAL_MS)))
        {
            return None;
        }
        return Some(Plan {
            id: s.id,
            segment,
            final_,
            audio: s.audio[segment.start..segment.end].to_vec(),
            keywords: s.keywords.clone(),
            cancel: s.cancel.clone(),
            stop_signal: s.stop_signal.clone(),
            cached,
        });
    }
}
fn run(model: &mut Model, shared: Shared) -> Result<(), String> {
    loop {
        let (lock, wake) = &*shared;
        let mut state = lock.lock().unwrap();
        if state.quit {
            return state.error.clone().map_or(Ok(()), Err);
        }
        if state
            .session
            .as_ref()
            .is_some_and(|s| s.cancel.load(Ordering::Acquire))
        {
            let s = state.session.take().unwrap();
            drop(state);
            output(json!({"type":"cancelled","id":s.id,"text":s.committed+s.draft.as_str(),"metrics":s.metrics})).map_err(|e|e.to_string())?;
            continue;
        }
        let work = state.session.as_mut().and_then(plan);
        if work.is_none() {
            if state.session.as_ref().is_some_and(|s| {
                s.stop && s.sealed && s.segments.pending.is_empty() && s.segments.draft().is_none()
            }) {
                let s = state.session.take().unwrap();
                drop(state);
                output(json!({"type":"complete","id":s.id,"text":s.committed,"metrics":s.metrics}))
                    .map_err(|e| e.to_string())?;
                continue;
            }
            if state.session.is_none() {
                drop(wake.wait(state).unwrap());
            } else {
                drop(wake.wait_timeout(state, Duration::from_millis(20)).unwrap());
            }
            continue;
        }
        let work = work.unwrap();
        drop(state);
        let begin = Instant::now();
        let result = if let Some(text) = work.cached.clone() {
            Ok(text)
        } else {
            let preview_stop = if !work.final_ {
                Some(work.stop_signal.as_ref())
            } else {
                None
            };
            model.decode(&work.audio, &work.keywords, &work.cancel, preview_stop)
        };
        let elapsed = begin.elapsed().as_secs_f64();
        let mut state = lock.lock().unwrap();
        let Some(s) = state.session.as_mut().filter(|s| s.id == work.id) else {
            continue;
        };
        if s.cancel.load(Ordering::Acquire) {
            continue;
        }
        if let Err(error) = &result {
            if retry_preempted_preview(work.final_, s.stop, error) {
                s.metrics.preempted_decodes += 1;
                s.metrics.preempted_decode_seconds += elapsed;
                s.metrics.decode_seconds += elapsed;
                continue;
            }
        }
        if s.draft_start != Some(work.segment.start) {
            s.draft_start = Some(work.segment.start);
            s.draft.clear();
        }
        let text = match result {
            Ok(v) => v,
            Err(_) => {
                let s = state.session.take().unwrap();
                drop(state);
                output(json!({"type":"error","id":s.id,"code":"native_decode","text":s.committed+s.draft.as_str(),"metrics":s.metrics})).map_err(|e|e.to_string())?;
                continue;
            }
        };
        if work.cached.is_none() {
            s.cache = Some((work.segment.start, work.segment.end, text.clone()));
            s.last_decode = Some(begin);
            s.metrics.decode_count += 1;
            s.metrics.decode_seconds += elapsed;
            s.metrics.last_decode_seconds = elapsed;
            s.metrics.max_decode_seconds = s.metrics.max_decode_seconds.max(elapsed);
            s.metrics.last_audio_seconds = work.audio.len() as f64 / RATE as f64;
            s.metrics.processed_audio_seconds = work.segment.end as f64 / RATE as f64;
            s.metrics.max_window_seconds = s
                .metrics
                .max_window_seconds
                .max(work.audio.len() as f64 / RATE as f64);
            s.metrics.max_backlog_seconds = s
                .metrics
                .max_backlog_seconds
                .max((s.audio.len() - work.segment.end) as f64 / RATE as f64);
        } else {
            s.metrics.cache_hits += 1;
        }
        if work.final_ && text.is_empty() && !s.draft.is_empty() {
            let s = state.session.take().unwrap();
            drop(state);
            output(json!({"type":"error","id":s.id,"code":"empty_final","text":s.committed+s.draft.as_str(),"metrics":s.metrics})).map_err(|e|e.to_string())?;
            continue;
        }
        if !text.is_empty() {
            s.draft = text;
        }
        let visible = s.committed.clone() + &s.draft;
        let metrics = s.metrics.clone();
        let age = s.started.elapsed().as_secs_f64();
        if work.final_ {
            s.committed = visible.clone();
            s.segments.pending.pop_front();
            s.draft_start = None;
            s.draft.clear();
        }
        drop(state);
        output(json!({"type":"caption","id":work.id,"text":visible,"final":work.final_,"metrics":metrics,"elapsed_seconds":age})).map_err(|e|e.to_string())?;
    }
}
fn main() -> std::process::ExitCode {
    match main_result() {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            let _ = output(json!({"type":"fatal","code":"startup_or_protocol","error":error}));
            std::process::ExitCode::FAILURE
        }
    }
}
fn main_result() -> Result<(), String> {
    let args: Vec<_> = std::env::args().collect();
    let mut model_path = None;
    let mut library = None;
    let mut threads = 6;
    let mut i = 1;
    while i < args.len() {
        if i + 1 >= args.len() {
            return Err("missing argument".into());
        }
        match args[i].as_str() {
            "--model" => model_path = Some(PathBuf::from(&args[i + 1])),
            "--library" => library = Some(PathBuf::from(&args[i + 1])),
            "--threads" => threads = args[i + 1].parse::<i32>().map_err(|_| "invalid threads")?,
            _ => return Err("unknown argument".into()),
        };
        i += 2;
    }
    if !(1..=8).contains(&threads) {
        return Err("invalid threads".into());
    }
    let opening = Instant::now();
    let api = unsafe { Api::open(library.as_deref()) }?;
    let library_seconds = opening.elapsed().as_secs_f64();
    let loading = Instant::now();
    let mut model = Model::load(api, &model_path.ok_or("model required")?, threads)?;
    let model_seconds = loading.elapsed().as_secs_f64();
    let warming = Instant::now();
    model
        .decode(&vec![0.; 8000], "", &AtomicBool::new(false), None)
        .map_err(|error| format!("{error:?}"))?;
    output(
        json!({"type":"ready","protocol":1,"engine":"haas-rust","device":"CPU","threads":threads,
            "library_seconds":library_seconds,"model_seconds":model_seconds,"warmup_seconds":warming.elapsed().as_secs_f64()}),
    )
    .map_err(|e| e.to_string())?;
    let shared: Shared = Arc::new((Mutex::new(State::default()), Condvar::new()));
    let input_shared = shared.clone();
    let active = model.active.clone();
    thread::spawn(move || input_thread(input_shared, active));
    run(&mut model, shared)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_an_interrupted_preview_at_stop_can_be_retried() {
        assert_eq!(
            retry_preempted_preview(false, true, &DecodeError::Interrupted),
            true
        );
        assert!(!retry_preempted_preview(
            true,
            true,
            &DecodeError::Interrupted
        ));
        assert!(!retry_preempted_preview(
            false,
            false,
            &DecodeError::Interrupted
        ));
        assert!(!retry_preempted_preview(
            false,
            true,
            &DecodeError::Native("failure".into())
        ));
    }
    #[test]
    fn stopping_a_work_item_never_stops_a_new_session() {
        let mut s = Session::new(1, String::new());
        s.audio = vec![0.2; RATE];
        let work = plan(&mut s).unwrap();
        s.stop_signal.store(true, Ordering::Release);
        assert!(work.stop_signal.load(Ordering::Acquire));
        assert!(!Session::new(2, String::new())
            .stop_signal
            .load(Ordering::Acquire));
        assert!(!work.cancel.load(Ordering::Acquire));
    }
    #[test]
    fn five_minute_stop_keeps_its_final_callback_tail_but_is_bounded() {
        assert!(audio_fits(300 * RATE, 32000));
        assert!(audio_fits(MAX_SAMPLES - 17, 34));
        assert!(!audio_fits(MAX_SAMPLES - 17, 36));
        assert!(!audio_fits(0, 32002));
    }
    #[test]
    fn complete_speech_with_only_short_silent_padding_reuses_its_caption() {
        let mut s = Session::new(1, String::new());
        s.audio = [vec![0.2; RATE], vec![0.; RATE]].concat();
        s.cache = Some((0, RATE * 14 / 10, "complete words".into()));
        let work = plan(&mut s).unwrap();
        assert!(work.final_);
        assert_eq!(work.cached.as_deref(), Some("complete words"));
        // A cached window including any further speech is never reused for a
        // shorter phrase, including the earlier cut at a short pause.
        s.audio[RATE * 12 / 10] = 0.2;
        assert!(!covers_final_tail(
            &s.audio,
            RATE * 14 / 10,
            RATE * 112 / 100
        ));
    }
    #[test]
    fn rejects_unknown_or_oversized_control() {
        assert!(serde_json::from_str::<Input>(r#"{"type":"start","id":1,"extra":1}"#).is_err());
        assert!(
            serde_json::from_str::<Input>(r#"{"type":"audio","id":1,"seq":-1,"pcm":""}"#).is_err()
        );
    }
    #[test]
    fn latest_audio_is_coalesced_before_a_decode() {
        let mut s = Session::new(1, String::new());
        s.audio = vec![0.2; RATE];
        let a = plan(&mut s).unwrap();
        assert_eq!(a.audio.len(), RATE);
        s.last_decode = None;
        s.audio.resize(3 * RATE, 0.2);
        let b = plan(&mut s).unwrap();
        assert_eq!(b.audio.len(), 3 * RATE);
    }
    #[test]
    fn sealed_stop_does_not_drop_a_small_tail() {
        let mut s = Session::new(1, String::new());
        s.audio = vec![0.2; RATE + 17];
        s.stop = true;
        let p = plan(&mut s).unwrap();
        assert!(p.final_);
        assert_eq!(p.audio.len(), RATE + 17);
    }
}
