use std::collections::VecDeque;
pub const RATE: usize = 16000;
pub const FRAME: usize = 320;
pub const MAX: usize = 12 * RATE;
const PAD: usize = 1920;
const SILENCE: usize = 9600;
#[derive(Clone, Copy, Debug)]
pub struct Segment {
    pub start: usize,
    pub end: usize,
    pub voiced: usize,
}
#[derive(Default)]
pub struct Segments {
    pub cursor: usize,
    start: Option<usize>,
    voice_end: usize,
    voiced: usize,
    floor: usize,
    quiet_start: Option<usize>,
    quiet_voiced: usize,
    cut: Option<(usize, usize)>,
    pub pending: VecDeque<Segment>,
}
impl Segments {
    pub fn feed(&mut self, audio: &[f32]) {
        for frame in audio.chunks(FRAME) {
            let end = self.cursor + frame.len();
            let voiced =
                frame.iter().map(|v| v * v).sum::<f32>() / frame.len() as f32 >= 0.004_f32.powi(2);
            if voiced {
                if self.start.is_none() {
                    self.start = Some(self.floor.max(self.cursor.saturating_sub(PAD)));
                    self.voiced = 0;
                    self.cut = None;
                }
                if let Some(q) = self.quiet_start {
                    if self.cursor - q >= PAD && q >= self.start.unwrap() + 8 * RATE {
                        self.cut =
                            Some(((q + self.cursor) / (2 * FRAME) * FRAME, self.quiet_voiced));
                    }
                }
                self.quiet_start = None;
                self.voice_end = end;
                self.voiced += frame.len();
            } else if self.quiet_start.is_none() {
                self.quiet_start = Some(self.cursor);
                self.quiet_voiced = self.voiced;
            }
            self.cursor = end;
            if let Some(start) = self.start {
                if end - start >= MAX {
                    let (cut, count) = self.cut.unwrap_or((end, self.voiced));
                    self.pending.push_back(Segment {
                        start,
                        end: cut,
                        voiced: count,
                    });
                    self.floor = cut;
                    self.start = if cut < end { Some(cut) } else { None };
                    self.voiced -= count;
                    self.cut = None;
                } else if end - self.voice_end >= SILENCE {
                    self.pending.push_back(Segment {
                        start,
                        end: end.min(self.voice_end + PAD),
                        voiced: self.voiced,
                    });
                    self.floor = end;
                    self.start = None;
                    self.voiced = 0;
                    self.cut = None;
                }
            }
        }
    }
    pub fn finish(&mut self) {
        if let Some(start) = self.start.take() {
            self.pending.push_back(Segment {
                start,
                end: self.cursor,
                voiced: self.voiced,
            });
            self.voiced = 0;
        }
    }
    pub fn draft(&self) -> Option<Segment> {
        self.start.map(|start| Segment {
            start,
            end: self.cursor,
            voiced: self.voiced,
        })
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn continuous_audio_is_bounded_and_never_skipped() {
        let a = vec![0.2; 30 * RATE];
        let mut s = Segments::default();
        s.feed(&a);
        s.finish();
        let p: Vec<_> = s.pending.into_iter().collect();
        assert_eq!(p.len(), 3);
        assert_eq!(p.iter().map(|p| p.end - p.start).sum::<usize>(), a.len());
        assert!(p.iter().all(|p| p.end - p.start <= MAX));
        assert!(p.windows(2).all(|p| p[0].end == p[1].start));
    }
    #[test]
    fn silence_yields_no_phrase() {
        let mut s = Segments::default();
        s.feed(&vec![0.; RATE]);
        s.finish();
        assert!(s.pending.is_empty());
    }
    #[test]
    fn short_pause_is_preferred_before_a_long_cut() {
        let mut a = vec![0.2; 9 * RATE];
        a.extend(vec![0.; 3200]);
        a.extend(vec![0.2; 10 * RATE]);
        let mut s = Segments::default();
        s.feed(&a);
        s.finish();
        let p: Vec<_> = s.pending.into_iter().collect();
        assert!(p[0].end < MAX);
        assert_eq!(p[0].end, p[1].start);
        assert_eq!(p.iter().map(|p| p.end - p.start).sum::<usize>(), a.len());
    }
    #[test]
    fn partial_end_preserves_last_samples() {
        let mut s = Segments::default();
        s.feed(&vec![0.2; RATE + 17]);
        s.finish();
        assert_eq!(s.pending[0].end, RATE + 17);
    }
    #[test]
    fn second_phrase_after_silence_has_valid_boundaries() {
        let mut a = vec![0.2; RATE];
        a.extend(vec![0.; RATE]);
        a.extend(vec![0.2; RATE]);
        let mut s = Segments::default();
        s.feed(&a);
        s.finish();
        assert_eq!(s.pending.len(), 2);
        assert!(s
            .pending
            .iter()
            .all(|v| v.end >= v.start && v.end - v.start <= MAX));
    }
}
