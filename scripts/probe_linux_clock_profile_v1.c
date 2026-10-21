/* Read-only Linux/glibc clock ABI probe; never sets a clock or NTP parameter. */
#define _GNU_SOURCE
#include <errno.h>
#include <stddef.h>
#include <stdio.h>
#include <sys/timex.h>
#include <time.h>
#include <gnu/libc-version.h>

#define OFFSET(name) printf("\"" #name "\":%zu,", offsetof(struct timex, name))

int main(void) {
    struct timex value = {0};
    struct timespec realtime = {0}, monotonic = {0}, boottime = {0};
    errno = 0;
    int realtime_rc = clock_gettime(CLOCK_REALTIME, &realtime);
    int realtime_errno = errno;
    errno = 0;
    int monotonic_rc = clock_gettime(CLOCK_MONOTONIC, &monotonic);
    int monotonic_errno = errno;
    errno = 0;
    int boottime_rc = clock_gettime(CLOCK_BOOTTIME, &boottime);
    int boottime_errno = errno;
    /* modes remains exactly zero: query only, including on an unhealthy host. */
    errno = 0;
    int state = adjtimex(&value);
    int adjtimex_errno = errno;
    printf("{\"schema_version\":\"ctcc.linux_clock_c_profile_probe.v1\",");
    printf("\"header_glibc\":\"%d.%d\",\"runtime_glibc\":\"%s\",", __GLIBC__, __GLIBC_MINOR__, gnu_get_libc_version());
    printf("\"long_size\":%zu,\"pointer_size\":%zu,\"timex_size\":%zu,\"timex_alignment\":%zu,", sizeof(long), sizeof(void *), sizeof(value), _Alignof(struct timex));
    printf("\"timespec_size\":%zu,\"timeval_size\":%zu,\"offsets\":{", sizeof(struct timespec), sizeof(struct timeval));
    OFFSET(modes); OFFSET(offset); OFFSET(freq); OFFSET(maxerror); OFFSET(esterror); OFFSET(status);
    OFFSET(constant); OFFSET(precision); OFFSET(tolerance); OFFSET(time); OFFSET(tick); OFFSET(ppsfreq);
    OFFSET(jitter); OFFSET(shift); OFFSET(stabil); OFFSET(jitcnt); OFFSET(calcnt); OFFSET(errcnt); OFFSET(stbcnt);
    printf("\"tai\":%zu},", offsetof(struct timex, tai));
    printf("\"clock_ids\":{\"realtime\":%d,\"monotonic\":%d,\"boottime\":%d},", CLOCK_REALTIME, CLOCK_MONOTONIC, CLOCK_BOOTTIME);
    printf("\"read_only_modes\":%u,\"state\":%d,\"status\":%d,\"maxerror_us\":%ld,\"esterror_us\":%ld,\"precision_us\":%ld,\"tolerance_scaled_ppm\":%ld,", value.modes, state, value.status, value.maxerror, value.esterror, value.precision, value.tolerance);
    printf("\"adjtimex_errno\":%d,\"clocks\":{", adjtimex_errno);
    printf("\"realtime\":{\"return_code\":%d,\"errno\":%d,\"seconds\":%ld,\"nanoseconds\":%ld},", realtime_rc, realtime_errno, realtime.tv_sec, realtime.tv_nsec);
    printf("\"monotonic\":{\"return_code\":%d,\"errno\":%d,\"seconds\":%ld,\"nanoseconds\":%ld},", monotonic_rc, monotonic_errno, monotonic.tv_sec, monotonic.tv_nsec);
    printf("\"boottime\":{\"return_code\":%d,\"errno\":%d,\"seconds\":%ld,\"nanoseconds\":%ld}},\"timex_raw_hex\":\"", boottime_rc, boottime_errno, boottime.tv_sec, boottime.tv_nsec);
    for (size_t i = 0; i < sizeof(value); i++) printf("%02x", ((unsigned char *)&value)[i]);
    printf("\"}\n");
    return 0;
}
