use crate::types::ScoredMarket;
use std::collections::BinaryHeap;

const TOP_N: usize = 10;

pub struct Ranker {
    heap: BinaryHeap<ScoredMarket>,
}

impl Ranker {
    pub fn new() -> Self {
        Ranker {
            heap: BinaryHeap::new(),
        }
    }

    pub fn push(&mut self, market: ScoredMarket) {
        self.heap.push(market);
        // sadece top-N tut
        if self.heap.len() > TOP_N {
            // todo: implement
            // en düşük confidence'ı çıkar
            // BinaryHeap max-heap → küçükleri atmak için min-heap trick
            // şimdilik basit: boyutu kontrol et
        }
    }

    pub fn top_n(&self) -> Vec<&ScoredMarket> {
        let mut items: Vec<&ScoredMarket> = self.heap.iter().collect();
        items.sort_by(|a, b| b.confidence.partial_cmp(&a.confidence).unwrap());
        items.truncate(TOP_N);
        items
    }

    pub fn clear(&mut self) {
        self.heap.clear();
    }
}