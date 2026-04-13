use crate::types::SignalSet;

const W_FAKE: f32 = 0.4;
const W_ABS: f32 = 0.4;
const W_PANIC: f32 = 0.2;

pub fn compute(s: &SignalSet) -> f32 {
    W_FAKE * s.fake_move + W_ABS * s.absorption + W_PANIC * s.panic
}

pub fn sigmoid(x: f32) -> f32 {
    1.0 / (1.0 + (-x * 10.0).exp())
    // *10 → skoru 0-1 arasına yayar
}