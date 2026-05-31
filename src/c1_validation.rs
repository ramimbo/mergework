
// C1 Control Character Validation
// Rejects characters in the C1 control range (0x80-0x9F)
fn reject_c1_control_chars(input: &str) -> Result<(), String> {
    for (i, ch) in input.chars().enumerate() {
        let code = ch as u32;
        if (0x80..=0x9F).contains(&code) {
            return Err(format!(
                "C1 control character U+{:04X} found at position {}",
                code, i
            ));
        }
    }
    Ok(())
}

fn validate_input(input: &str) -> Result<(), String> {
    reject_c1_control_chars(input)?;
    // Additional validation...
    if input.len() > 10000 {
        return Err("Input exceeds maximum length".to_string());
    }
    Ok(())
}
