use std::path::{Path, PathBuf};

fn main() {
    // pyo3 links libpython, but a python-build-standalone interpreter records its
    // build-time lib dir (/install/lib) in sysconfig, so the linker can't find
    // libpython at our actual location. Derive the real lib dir from PYO3_PYTHON
    // (set by `reflex-desktop build`) and add it to the link search path, plus
    // rpath entries covering both the dev layout (target/) and the installed
    // bundle layout, so the shipped binary resolves the *bundled* libpython on
    // any machine.
    println!("cargo:rerun-if-env-changed=PYO3_PYTHON");
    if let Ok(py) = std::env::var("PYO3_PYTHON") {
        configure_libpython_linking(&PathBuf::from(py));
    }
    tauri_build::build();
}

fn configure_libpython_linking(interpreter: &Path) {
    let target_os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    if target_os == "windows" {
        // Windows has no rpath: pyo3 links the import library (found via the
        // interpreter's sysconfig) and the loader resolves python3XY.dll at startup
        // from the executable's directory. The bundle maps python/python/python3*.dll
        // next to the exe (see tauri.conf.json resources); `reflex-desktop run` puts
        // the interpreter dir on PATH for dev binaries in target/.
        return;
    }

    let Some(lib) = interpreter
        .parent()
        .and_then(|bin| bin.parent())
        .map(|root| root.join("lib"))
    else {
        return;
    };
    if !lib.is_dir() {
        return;
    }
    println!("cargo:rustc-link-search=native={}", lib.display());
    // Dev binaries run from target/ resolve libpython via this absolute rpath.
    println!("cargo:rustc-link-arg=-Wl,-rpath,{}", lib.display());

    if target_os == "macos" {
        // Installed .app: Contents/MacOS/<bin> loads Contents/Resources/python/....
        // The bundled dylib's install name is rewritten to @rpath/libpythonX.Y.dylib
        // at assemble time (runtime.py), so these rpaths fully determine resolution.
        println!(
            "cargo:rustc-link-arg=-Wl,-rpath,@executable_path/../Resources/python/python/lib"
        );
    } else {
        // deb/rpm install the binary at /usr/bin/<bin> with resources at
        // /usr/lib/<bin>/; an AppImage mirrors the same usr/ tree inside its AppDir.
        let bin_name = std::env::var("CARGO_PKG_NAME").unwrap_or_default();
        println!(
            "cargo:rustc-link-arg=-Wl,-rpath,$ORIGIN/../lib/{bin_name}/python/python/lib"
        );
    }
}
