use std::{env, path::PathBuf};
fn main() {
    println!("cargo:rerun-if-env-changed=SPEECH_STATIC_DIR");
    if !env::var("TARGET")
        .unwrap_or_default()
        .contains("windows-gnu")
    {
        return;
    }
    let root =
        PathBuf::from(env::var_os("SPEECH_STATIC_DIR").expect("SPEECH_STATIC_DIR is required"));
    let libs = [
        "libspeech-static.a",
        "libqwen3tts.a",
        "libirodoritts.a",
        "libdrama3.a",
        "libfastconformer.a",
        "libqwen3asr.a",
        "libnemotrontranscribe.a",
        "libsilerovad.a",
        "libsortformer.a",
        "libspeechcommon.a",
        "ggml/src/ggml.a",
        "ggml/src/ggml-cpu.a",
        "ggml/src/ggml-base.a",
    ];
    println!("cargo:rustc-link-arg=-Wl,--start-group");
    for lib in libs {
        let path = root.join(lib);
        assert!(path.is_file(), "missing {}", path.display());
        println!("cargo:rustc-link-arg={}", path.display());
    }
    for lib in [
        "libstdc++.a",
        "libwinpthread.a",
        "libgcc.a",
        "libgcc_eh.a",
        "libmingwex.a",
        "libucrt.a",
        "libkernel32.a",
        "libadvapi32.a",
        "libshell32.a",
    ] {
        let result = std::process::Command::new("x86_64-w64-mingw32-g++")
            .arg(format!("-print-file-name={lib}"))
            .output()
            .expect("MinGW compiler");
        let path = String::from_utf8(result.stdout).unwrap();
        assert!(
            result.status.success() && PathBuf::from(path.trim()).is_file(),
            "missing compiler archive: {lib}"
        );
        println!("cargo:rustc-link-arg={}", path.trim());
    }
    println!("cargo:rustc-link-arg=-Wl,--end-group");
    println!("cargo:rustc-link-lib=shell32");
    println!("cargo:rustc-link-arg=-Wl,--no-insert-timestamp");
}
