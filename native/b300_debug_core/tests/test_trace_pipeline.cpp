#include "b300/trace_pipeline.hpp"

#include <cassert>
#include <cstdint>
#include <stdexcept>

int main() {
    b300::FixedWidthValueDecoder decoder(7);
    b300::EventBuffer buffer(8);
    b300::TracePipeline pipeline(decoder, buffer, 16);

    const std::uint8_t payload[] = {
        1, 0, 0, 0,
        2, 0, 0, 0,
        9, 9,
    };

    const auto result = pipeline.ingest(payload, sizeof(payload), {1000, 42});
    assert(result.bytes_consumed == 8);
    assert(result.events_decoded == 2);
    assert(result.dropped_total == 0);
    assert(pipeline.max_input_bytes() == 16);

    const auto events = buffer.drain(8);
    assert(events.size() == 2);
    assert(events[0].timestamp_ns == 1000);
    assert(events[0].source_id == 42);
    assert(events[0].channel == 7);
    assert(events[0].value == 1);
    assert(events[1].value == 2);

    bool oversize_failed_closed = false;
    try {
        const std::uint8_t oversized[17]{};
        (void)pipeline.ingest(oversized, sizeof(oversized), {0, 0});
    } catch (const std::length_error&) {
        oversize_failed_closed = true;
    }
    assert(oversize_failed_closed);

    bool zero_bound_rejected = false;
    try {
        b300::TracePipeline invalid(decoder, buffer, 0);
        (void)invalid;
    } catch (const std::invalid_argument&) {
        zero_bound_rejected = true;
    }
    assert(zero_bound_rejected);

    return 0;
}
