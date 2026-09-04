#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "b300/trace_event.hpp"

namespace b300 {

struct DecodeContext {
    std::uint64_t timestamp_ns{};
    std::uint32_t source_id{};
};

class ITraceDecoder {
public:
    virtual ~ITraceDecoder() = default;

    virtual std::size_t decode(
        const std::uint8_t* data,
        std::size_t size,
        const DecodeContext& context,
        std::vector<TraceEvent>& out
    ) = 0;
};

// Synthetic reference decoder used to lock the native contract before
// hardware-specific SWO/ITM/RTT decoders are introduced.
class FixedWidthValueDecoder final : public ITraceDecoder {
public:
    explicit FixedWidthValueDecoder(std::uint16_t channel = 0) noexcept;

    std::size_t decode(
        const std::uint8_t* data,
        std::size_t size,
        const DecodeContext& context,
        std::vector<TraceEvent>& out
    ) override;

private:
    std::uint16_t channel_{};
};

} // namespace b300
