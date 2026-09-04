#include "b300/trace_pipeline.hpp"

#include <stdexcept>

namespace b300 {

TracePipeline::TracePipeline(
    ITraceDecoder& decoder,
    EventBuffer& buffer,
    std::size_t max_input_bytes
)
    : decoder_(decoder),
      buffer_(buffer),
      max_input_bytes_(max_input_bytes) {
    if (max_input_bytes_ == 0) {
        throw std::invalid_argument("TracePipeline max_input_bytes must be greater than zero");
    }
}

IngestResult TracePipeline::ingest(
    const std::uint8_t* data,
    std::size_t size,
    const DecodeContext& context
) {
    if (size > max_input_bytes_) {
        throw std::length_error("TracePipeline input exceeds configured bound");
    }

    scratch_.clear();
    const auto consumed = decoder_.decode(data, size, context, scratch_);
    if (consumed > size) {
        throw std::runtime_error("trace decoder consumed more bytes than supplied");
    }

    for (const auto& event : scratch_) {
        buffer_.push(event);
    }

    return {
        consumed,
        scratch_.size(),
        buffer_.dropped(),
    };
}

std::size_t TracePipeline::max_input_bytes() const noexcept {
    return max_input_bytes_;
}

} // namespace b300
