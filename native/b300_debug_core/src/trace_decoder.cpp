#include "b300/trace_decoder.hpp"

#include <stdexcept>

namespace b300 {

FixedWidthValueDecoder::FixedWidthValueDecoder(std::uint16_t channel) noexcept
    : channel_(channel) {}

std::size_t FixedWidthValueDecoder::decode(
    const std::uint8_t* data,
    std::size_t size,
    const DecodeContext& context,
    std::vector<TraceEvent>& out
) {
    if (size != 0 && data == nullptr) {
        throw std::invalid_argument("decoder input cannot be null when size is non-zero");
    }

    constexpr std::size_t width = sizeof(std::uint32_t);
    const auto count = size / width;
    out.reserve(out.size() + count);

    for (std::size_t index = 0; index < count; ++index) {
        const auto offset = index * width;
        const std::uint64_t value =
            static_cast<std::uint64_t>(data[offset]) |
            (static_cast<std::uint64_t>(data[offset + 1]) << 8U) |
            (static_cast<std::uint64_t>(data[offset + 2]) << 16U) |
            (static_cast<std::uint64_t>(data[offset + 3]) << 24U);

        out.push_back({
            context.timestamp_ns + index,
            context.source_id,
            channel_,
            TraceEventType::sample,
            value,
        });
    }

    return count * width;
}

} // namespace b300
