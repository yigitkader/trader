use std::collections::VecDeque;

// Sliding window: son 5 dakikadaki fiyat değişimi
// VecDeque → önden pop, arkadan push → O(1)

const WINDOW_SECS: u64 = 300; // 5 dakika

pub fn compute(window: &VecDeque<(u64, f32)>) -> f32 {
    if window.len() < 2 {
        return 0.0;
    }

    let now = window.back().unwrap();
    let cutoff = now.0.saturating_sub(WINDOW_SECS);

    // window'daki en eski geçerli fiyatı bul
    let oldest = window
        .iter()
        .find(|(ts, _)| *ts >= cutoff);

    match oldest {
        Some(old) => now.1 - old.1,
        None => 0.0,
    }
}

pub fn push_price(window: &mut VecDeque<(u64, f32)>, ts: u64, price: f32) {
    let cutoff = ts.saturating_sub(WINDOW_SECS);
    // eski veriyi temizle
    while window.front().map(|(t, _)| *t < cutoff).unwrap_or(false) {
        window.pop_front();
    }
    window.push_back((ts, price));
}