#include "b300/sample_buffer.hpp"

#include <algorithm>
#include <stdexcept>

namespace b300 {

SampleBuffer::SampleBuffer(std::size_t capacity) : capacity_(capacity) {
    if (capacity_ == 0) {
        throw std::invalid_argument("SampleBuffer capacity must be greater than zero");
    }
}

void SampleBuffer::push(const SamplePoint& sample) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (queue_.size() >= capacity_) {
        queue_.pop_front();
        ++dropped_;
    }
    queue_.push_back(sample);
}

std::vector<SamplePoint> SampleBuffer::drain(std::size_t max_items) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto count = std::min(max_items, queue_.size());
    std::vector<SamplePoint> out;
    out.reserve(count);
    for (std::size_t index = 0; index < count; ++index) {
        out.push_back(queue_.front());
        queue_.pop_front();
    }
    return out;
}

std::size_t SampleBuffer::size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return queue_.size();
}

std::size_t SampleBuffer::capacity() const noexcept {
    return capacity_;
}

std::uint64_t SampleBuffer::dropped() const noexcept {
    std::lock_guard<std::mutex> lock(mutex_);
    return dropped_;
}

} // namespace b300
