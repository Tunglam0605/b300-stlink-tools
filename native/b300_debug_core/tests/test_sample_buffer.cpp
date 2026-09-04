#include "b300/sample_buffer.hpp"

#include <cassert>

int main() {
    b300::SampleBuffer buffer(3);
    assert(buffer.capacity() == 3);
    assert(buffer.size() == 0);
    assert(buffer.dropped() == 0);

    buffer.push({1, 10, 100});
    buffer.push({2, 11, 101});
    buffer.push({3, 12, 102});
    buffer.push({4, 13, 103});

    assert(buffer.size() == 3);
    assert(buffer.dropped() == 1);

    const auto first = buffer.drain(2);
    assert(first.size() == 2);
    assert(first[0].timestamp_ns == 2);
    assert(first[1].timestamp_ns == 3);

    const auto second = buffer.drain(8);
    assert(second.size() == 1);
    assert(second[0].timestamp_ns == 4);
    assert(buffer.size() == 0);
    assert(buffer.dropped() == 1);

    return 0;
}
