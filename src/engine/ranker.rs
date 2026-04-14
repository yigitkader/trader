use crate::types::ScoredMarket;

pub const TOP_N: usize = 10;

/// Tick içinde en fazla `TOP_N` aday tutar (tüm piyasaları heap’te biriktirmez).
pub struct Ranker {
    items: Vec<ScoredMarket>,
}

impl Ranker {
    pub fn new() -> Self {
        Ranker {
            items: Vec::with_capacity(TOP_N),
        }
    }

    pub fn push(&mut self, market: ScoredMarket) {
        if market.edge_score.is_nan()
            || market.confidence.is_nan()
            || market.annualized_edge.is_nan()
        {
            return;
        }
        if self.items.len() < TOP_N {
            self.items.push(market);
            return;
        }
        let Some((worst_i, _)) = self
            .items
            .iter()
            .enumerate()
            .min_by(|(_, a), (_, b)| {
                a.annualized_edge
                    .abs()
                    .partial_cmp(&b.annualized_edge.abs())
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
        else {
            return;
        };
        if market.annualized_edge.abs() > self.items[worst_i].annualized_edge.abs() {
            self.items[worst_i] = market;
        }
    }

    pub fn top_n(&self) -> Vec<&ScoredMarket> {
        let mut items: Vec<&ScoredMarket> = self.items.iter().collect();
        items.sort_by(|a, b| {
            b.annualized_edge
                .abs()
                .partial_cmp(&a.annualized_edge.abs())
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        items
    }

    pub fn clear(&mut self) {
        self.items.clear();
    }
}
