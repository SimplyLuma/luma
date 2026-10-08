// SPDX-License-Identifier: Apache-2.0
// Read-only resource decoding; the caller owns ZIP validation and worker limits.
use apk_info_axml::{ARSC, AXML};
fn read_bounded(path: &str, limit: u64) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    use std::io::Read;
    let mut bytes = Vec::new();
    std::fs::File::open(path)?.take(limit + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit { return Err("metadata exceeds its limit".into()); }
    Ok(bytes)
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let a: Vec<String> = std::env::args().collect();
    if a.len() != 3 { return Err("expected manifest and resources paths".into()); }
    let manifest = read_bounded(&a[1], 4 * 1024 * 1024)?;
    let resources = read_bounded(&a[2], 64 * 1024 * 1024)?;
    let arsc = ARSC::new(&mut resources.as_slice())?;
    let axml = AXML::new(&mut manifest.as_slice(), Some(&arsc))?;
    let label = axml.get_attribute_value("application", "label", Some(&arsc));
    let icon = axml.get_attribute_value("application", "icon", Some(&arsc));
    if label.as_ref().is_some_and(|s| s.len() > 1024) ||
       icon.as_ref().is_some_and(|s| s.len() > 4096) {
        return Err("metadata value exceeds its limit".into());
    }
    println!("{}", serde_json::json!({"label": label, "icon": icon}));
    Ok(())
}
