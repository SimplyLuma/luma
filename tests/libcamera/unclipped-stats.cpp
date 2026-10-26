// SPDX-License-Identifier: Apache-2.0
// Exercise the patched CPU ISP statistics macros, including legacy metering.

#include <cassert>
#include <array>
#include <numeric>
#include "libcamera/internal/software_isp/swisp_stats.h"
using namespace libcamera;
#include "statistics-macros.h"
struct Sampler {
 struct {unsigned x=0,y=0,width=12,height=12;} window_;
 SwIspStats shared{};
 SwIspStats *sharedStats_ = &shared;
 std::array<SwIspStats, 2> stats_{};
 unsigned sumShift_ = 0;
 static constexpr unsigned kStatPerNumFrames = 4;
 struct {void emit(uint32_t, uint32_t) {}} statsReady;
 void finishFrame(uint32_t frame, uint32_t bufferId);
 SwIspStats sample(const std::array<std::array<uint16_t,3>,3>& pixels, unsigned div) {
  SwIspStats stats{}; unsigned y=6;
  SWSTATS_START_LINE_STATS(uint16_t, 12)
  for (unsigned x=0;x<3;x++) {
   r=pixels[x][0];g=pixels[x][1];b=pixels[x][2];g2=g;
   SWSTATS_ACCUMULATE_LINE_STATS(div)
  }
  SWSTATS_FINISH_LINE_STATS()
  return stats;
 }
};
#include "finish-statistics.h"
int main() {
 Sampler sampler;
 for(unsigned div: {1u,2u,4u}) {
  const uint16_t d=div;
  auto stats=sampler.sample({{{uint16_t(90*d),uint16_t(160*d),uint16_t(110*d)},
                            {uint16_t(255*d),uint16_t(255*d),uint16_t(255*d)},
                            {uint16_t(250*d),uint16_t(150*d),uint16_t(120*d)}}},div);
  sampler.stats_ = {stats, stats};
  sampler.sumShift_ = div == 4 ? 2 : div == 2 ? 1 : 0;
  sampler.finishFrame(0, 1);
  assert(sampler.shared.valid && sampler.shared.awbSamples_ == 2);
  assert(sampler.shared.awbSum_.r() == 180 && sampler.shared.awbSum_.g() == 320);
  assert(sampler.shared.sum_.r() == 1190 && sampler.shared.sum_.g() == 1130);
  assert(std::accumulate(sampler.shared.yHistogram.begin(), sampler.shared.yHistogram.end(), 0u) == 6);
  sampler.finishFrame(1, 1);
  assert(!sampler.shared.valid);
  sampler.finishFrame(4, 1);
  assert(sampler.shared.valid && sampler.shared.awbSamples_ == 2);
  assert(sampler.shared.awbSum_.r() == 180);
  assert(stats.awbSamples_==1);
  assert(stats.awbSum_.r()==90*div && stats.awbSum_.g()==160*div && stats.awbSum_.b()==110*div);
  assert(stats.sum_.r()==595*div && stats.sum_.g()==565*div && stats.sum_.b()==485*div);
  assert(std::accumulate(stats.yHistogram.begin(),stats.yHistogram.end(),0u)==3);
  assert(std::accumulate(stats.yHistogramGrid[3].begin(),stats.yHistogramGrid[3].end(),0u)==3);
  auto clipped=sampler.sample({{{uint16_t(248*d),0,0},{0,uint16_t(248*d),0},{0,0,uint16_t(255*d)}}},div);
  assert(clipped.awbSamples_==0);
  assert(std::accumulate(clipped.yHistogram.begin(),clipped.yHistogram.end(),0u)==3);
  auto below=sampler.sample({{{uint16_t(247*d),uint16_t(247*d),uint16_t(247*d)},
                            {uint16_t(247*d),uint16_t(247*d),uint16_t(247*d)},
                            {uint16_t(247*d),uint16_t(247*d),uint16_t(247*d)}}},div);
  assert(below.awbSamples_==3 && below.awbSum_.r()==741*div);
 }
}
