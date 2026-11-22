fn main() {
    // The BLAS backend's macOS seam calls Accelerate (cblas_sgemm + vForce). Link the framework
    // only when that backend is actually compiled: feature on AND target is macOS. A default build
    // (feature off) links nothing here; the Linux seam is pure Rust (faer) and needs no link.
    let blas = std::env::var("CARGO_FEATURE_BLAS").is_ok();
    let target_os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    if blas && target_os == "macos" {
        println!("cargo:rustc-link-lib=framework=Accelerate");
    }
    // Windows port: the main thread gets 1 MB on Windows against 8 MB on Linux/macOS, and the
    // indexing path (embedding + lance) overflows it. Match the unix default for the binaries.
    if target_os == "windows" && std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        println!("cargo:rustc-link-arg-bins=/STACK:8388608");
    }
}
