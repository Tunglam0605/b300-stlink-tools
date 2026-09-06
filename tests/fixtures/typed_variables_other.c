#include <stdint.h>

static uint32_t duplicate_static;

uint32_t typed_other_value(void) {
    return duplicate_static;
}
