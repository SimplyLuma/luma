// SPDX-License-Identifier: Apache-2.0
// Real licensed voice/model inference; never substitute prerecorded silence.
#include <piper.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
int main(int argc, char **argv) {
 if (argc != 3) return 2;
 auto *s = piper_create(argv[1], argv[2], "/usr/share/espeak-ng-data");
 if (!s) return 3;
 const char *phrases[] = {"Welcome to Luma. This is a natural voice.", "It's safe: apostrophes, dollar signs, and quoted text are words."};
 for (auto *text: phrases) {
  const auto start = std::chrono::steady_clock::now();
  auto options = piper_default_synthesize_options(s);
  if (piper_synthesize_start(s, text, &options) != PIPER_OK) return 4;
  piper_audio_chunk chunk{}; size_t count = 0; float peak = 0;
  do {
   auto status = piper_synthesize_next(s, &chunk);
   if (status != PIPER_OK && status != PIPER_DONE) return 5;
   if (chunk.sample_rate != 22050) return 6;
   for(size_t i=0;i<chunk.num_samples;i++) {
    if (!std::isfinite(chunk.samples[i])) return 7;
    peak=std::max(peak,std::abs(chunk.samples[i]));
   }
   count += chunk.num_samples;
  } while (!chunk.is_last);
  if(count < 22050 || peak < 0.01f) return 8;
  auto elapsed=std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now()-start).count();
  std::printf("PIPER REAL SYNTHESIS PASS samples=%zu peak=%.3f elapsed_ms=%lld\n", count, peak, (long long)elapsed);
 }
 piper_free(s); return 0;
}
