#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "b300/trace_decoder.hpp"

namespace b300 {

// Streaming decoder for ITM source packets used by software stimulus ports.
//
// This decoder deliberately emits software instrumentation packets only.
// Hardware-source and protocol packets are consumed and counted but are not
// interpreted here. That keeps the initial native path bounded and prevents
// target-control/timestamp policy from leaking into the data-plane layer.
class ItmStimulusDecoder final : public ITraceDecoder {
public:
    std::size_t decode(
        const std::uint8_t* data,
        std::size_t size,
        const DecodeContext& context,
        std::vector<TraceEvent>& out
    ) override;

    std::size_t pending_payload_bytes() const noexcept;
    std::uint64_t protocol_packets_skipped() const noexcept;
    std::uint64_t hardware_packets_skipped() const noexcept;

private:
    void start_source_packet(std::uint8_t header) noexcept;
    void finish_packet(const DecodeContext& context, std::vector<TraceEvent>& out);
    void reset_packet() noexcept;

    std::array<std::uint8_t, 4> payload_{};
    std::size_t expected_payload_{0};
    std::size_t received_payload_{0};
    std::uint16_t channel_{0};
    bool software_packet_{false};
    std::uint64_t emitted_count_{0};
    std::uint64_t protocol_packets_skipped_{0};
    std::uint64_t hardware_packets_skipped_{0};
};

} // namespace b300
