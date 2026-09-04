#include "b300/event_buffer.hpp"

#include <cassert>
#include <cstdint>

int main() {
    b300::EventBuffer buffer(2);
    buffer.push({1, 10, 1, b300::TraceEventType::itm_data, 100});
    buffer.push({2, 10, 2, b300::TraceEventType::sample, 200});
    buffer.push({3, 10, 3, b300::TraceEventType::task_switch, 300});

    assert(buffer.size() == 2);
    assert(buffer.capacity() == 2);
    assert(buffer.dropped() == 1);

    const auto first = buffer.drain(1);
    assert(first.size() == 1);
    assert(first[0].timestamp_ns == 2);
    assert(first[0].value == 200);

    const auto rest = buffer.drain(8);
    assert(rest.size() == 1);
    assert(rest[0].timestamp_ns == 3);
    assert(rest[0].type == b300::TraceEventType::task_switch);
    assert(buffer.size() == 0);
    return 0;
}
