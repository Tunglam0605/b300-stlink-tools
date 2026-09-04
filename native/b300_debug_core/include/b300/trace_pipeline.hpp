#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "b300/event_buffer.hpp"
#include "b300/trace_decoder.hpp"

namespace b300 {

struct IngestResult {
    std::size_t bytes_consumed{};
    std::size_t events_decoded{};
    std::uint64_t dropped_total{};
};

class TracePipeline final {
public:
    TracePipeline(ITraceDecoder& decoder, EventBuffer& buffer, std::size_t max_input_bytes);

    IngestResult ingest(
        const std::uint8_t* data,
        std::size_t size,
        const DecodeContext& context
    );

    std::size_t max_input_bytes() const noexcept;

private:
    ITraceDecoder& decoder_;
    EventBuffer& buffer_;
    const std::size_t max_input_bytes_;
    std::vector<TraceEvent> scratch_;
};

} // namespace b300
