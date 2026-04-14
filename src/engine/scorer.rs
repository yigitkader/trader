use crate::types::SignalSet;

const W_FAKE: f32 = 0.32;
const W_ABS: f32 = 0.32;
const W_PANIC: f32 = 0.16;
const W_BOOK: f32 = 0.20;

/// Weighted sum of SIGNED signals. Positive → bullish YES, negative → bearish YES.
pub fn compute(s: &SignalSet) -> f32 {
    W_FAKE * s.fake_move
        + W_ABS * s.absorption
        + W_PANIC * s.panic
        + W_BOOK * s.book_skew
}

/// Maps signed edge to (0, 1). edge=0 → 0.5, positive → >0.5, negative → <0.5.
pub fn sigmoid(x: f32) -> f32 {
    1.0 / (1.0 + (-x * 4.0).exp())
}
