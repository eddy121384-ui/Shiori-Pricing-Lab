#include "shiori_rates/dto/sha256.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

namespace Shiori::rates::dto {

namespace {

constexpr std::array<std::uint32_t, 64> kRoundConstants = {
    0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU, 0x59f111f1U, 0x923f82a4U,
    0xab1c5ed5U, 0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U, 0x72be5d74U, 0x80deb1feU,
    0x9bdc06a7U, 0xc19bf174U, 0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU, 0x2de92c6fU,
    0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU, 0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
    0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU,
    0x53380d13U, 0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U, 0xa2bfe8a1U, 0xa81a664bU,
    0xc24b8b70U, 0xc76c51a3U, 0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U, 0x19a4c116U,
    0x1e376c08U, 0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
    0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U, 0x90befffaU, 0xa4506cebU, 0xbef9a3f7U,
    0xc67178f2U};

constexpr std::array<std::uint32_t, 8> kInitialState = {0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U,
                                                        0xa54ff53aU, 0x510e527fU, 0x9b05688cU,
                                                        0x1f83d9abU, 0x5be0cd19U};

constexpr std::uint32_t rotr(std::uint32_t value, std::uint32_t shift) noexcept {
  return (value >> shift) | (value << (32U - shift));
}

void compress(std::array<std::uint32_t, 8>& state, const std::uint8_t* block) noexcept {
  std::array<std::uint32_t, 64> schedule{};
  for (std::size_t index = 0; index < 16; ++index) {
    const std::size_t base = index * 4;
    schedule[index] = (static_cast<std::uint32_t>(block[base]) << 24U) |
                      (static_cast<std::uint32_t>(block[base + 1]) << 16U) |
                      (static_cast<std::uint32_t>(block[base + 2]) << 8U) |
                      static_cast<std::uint32_t>(block[base + 3]);
  }
  for (std::size_t index = 16; index < 64; ++index) {
    const std::uint32_t s0 = rotr(schedule[index - 15], 7U) ^ rotr(schedule[index - 15], 18U) ^
                             (schedule[index - 15] >> 3U);
    const std::uint32_t s1 = rotr(schedule[index - 2], 17U) ^ rotr(schedule[index - 2], 19U) ^
                             (schedule[index - 2] >> 10U);
    schedule[index] = schedule[index - 16] + s0 + schedule[index - 7] + s1;
  }

  std::uint32_t a = state[0];
  std::uint32_t b = state[1];
  std::uint32_t c = state[2];
  std::uint32_t d = state[3];
  std::uint32_t e = state[4];
  std::uint32_t f = state[5];
  std::uint32_t g = state[6];
  std::uint32_t h = state[7];

  for (std::size_t index = 0; index < 64; ++index) {
    const std::uint32_t sigma1 = rotr(e, 6U) ^ rotr(e, 11U) ^ rotr(e, 25U);
    const std::uint32_t choose = (e & f) ^ ((~e) & g);
    const std::uint32_t temp1 = h + sigma1 + choose + kRoundConstants[index] + schedule[index];
    const std::uint32_t sigma0 = rotr(a, 2U) ^ rotr(a, 13U) ^ rotr(a, 22U);
    const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
    const std::uint32_t temp2 = sigma0 + majority;

    h = g;
    g = f;
    f = e;
    e = d + temp1;
    d = c;
    c = b;
    b = a;
    a = temp1 + temp2;
  }

  state[0] += a;
  state[1] += b;
  state[2] += c;
  state[3] += d;
  state[4] += e;
  state[5] += f;
  state[6] += g;
  state[7] += h;
}

}  // namespace

Sha256Digest sha256(const std::string_view data) {
  auto state = kInitialState;

  const auto* bytes = reinterpret_cast<const std::uint8_t*>(data.data());
  const std::size_t length = data.size();
  const std::size_t full_blocks = length / 64U;

  for (std::size_t block = 0; block < full_blocks; ++block) {
    compress(state, bytes + (block * 64U));
  }

  // Final block(s): remaining bytes + 0x80 + zero padding + 64-bit big-endian bit length.
  const std::size_t remainder = length - (full_blocks * 64U);
  std::array<std::uint8_t, 128> tail{};
  for (std::size_t index = 0; index < remainder; ++index) {
    tail[index] = bytes[(full_blocks * 64U) + index];
  }
  tail[remainder] = 0x80U;

  const std::size_t tail_blocks = (remainder + 1U + 8U > 64U) ? 2U : 1U;
  const std::size_t length_offset = (tail_blocks * 64U) - 8U;
  const std::uint64_t bit_length = static_cast<std::uint64_t>(length) * 8U;
  for (std::size_t index = 0; index < 8; ++index) {
    tail[length_offset + index] =
        static_cast<std::uint8_t>((bit_length >> ((7U - index) * 8U)) & 0xFFU);
  }
  for (std::size_t block = 0; block < tail_blocks; ++block) {
    compress(state, tail.data() + (block * 64U));
  }

  Sha256Digest digest{};
  for (std::size_t index = 0; index < 8; ++index) {
    digest[index * 4] = static_cast<std::uint8_t>((state[index] >> 24U) & 0xFFU);
    digest[(index * 4) + 1] = static_cast<std::uint8_t>((state[index] >> 16U) & 0xFFU);
    digest[(index * 4) + 2] = static_cast<std::uint8_t>((state[index] >> 8U) & 0xFFU);
    digest[(index * 4) + 3] = static_cast<std::uint8_t>(state[index] & 0xFFU);
  }
  return digest;
}

std::string to_hex(const Sha256Digest& digest) {
  static constexpr char kHex[] = "0123456789abcdef";
  std::string out;
  out.resize(digest.size() * 2U);
  for (std::size_t index = 0; index < digest.size(); ++index) {
    out[index * 2] = kHex[(digest[index] >> 4U) & 0x0FU];
    out[(index * 2) + 1] = kHex[digest[index] & 0x0FU];
  }
  return out;
}

std::string sha256_hex(const std::string_view data) { return to_hex(sha256(data)); }

}  // namespace Shiori::rates::dto
