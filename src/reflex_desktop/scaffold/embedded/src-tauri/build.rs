use std::path::PathBuf;

fn main() {
    // pyo3 links libpython, but a python-build-standalone interpreter records its
    // build-time lib dir (/install/lib) in sysconfig, so the linker can't find
    // libpython at our actual location. Derive the real lib dir from PYO3_PYTHON
    // (set by `reflex-desktop build`) and add it to the link search path + rpath.
    //
    // The absolute rpath makes a dev binary (run from target/) work. Production
    // bundles relocate the interpreter into app resources and need a $ORIGIN-relative
    // rpath or an AppRun/launcher that sets LD_LIBRARY_PATH — see the README (M2).
    if let Ok(py) = std::env::var("PYO3_PYTHON") {
        let interp = PathBuf::from(py);
        if let Some(lib) = interp
            .parent()
            .and_then(|bin| bin.parent())
            .map(|root| root.join("lib"))
        {
            if lib.is_dir() {
                println!("cargo:rustc-link-search=native={}", lib.display());
                println!("cargo:rustc-link-arg=-Wl,-rpath,{}", lib.display());
            }
        }
    }
    tauri_build::build();
}
