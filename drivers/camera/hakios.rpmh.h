/*
 * Shared SM8750 (hakios) RPMh CMD_DB lookup and Apps RSC DRV2 (0x16520000) active TCS helper.
 *
 * Serializes active TCS0..2 VRM regulator and ARC power-domain level transactions across
 * ADSP, audio smart amplifiers, CAMSS, and Iris VPU drivers.
 */
#ifndef _SYS_HAKIOS_RPMH_H_
#define _SYS_HAKIOS_RPMH_H_

#include <stdint.h>
#include <stdbool.h>

#ifdef KERNEL

/* RPMh VRM regulator modes (PMIC5 / PM8550 / PMH8550). */
#define HAKIOS_RPMH_MODE_NONE 0u
#define HAKIOS_RPMH_MODE_LPM  4u
#define HAKIOS_RPMH_MODE_AUTO 6u
#define HAKIOS_RPMH_MODE_HPM  7u

/* RPMh ARC power-domain corner levels (dt-bindings/power/qcom-rpmpd.h). */
#define HAKIOS_RPMPD_LEVEL_OFF        0u
#define HAKIOS_RPMPD_LEVEL_RETENTION  16u
#define HAKIOS_RPMPD_LEVEL_MIN_SVS    48u
#define HAKIOS_RPMPD_LEVEL_LOW_SVS    64u
#define HAKIOS_RPMPD_LEVEL_SVS        128u
#define HAKIOS_RPMPD_LEVEL_SVS_L1     192u
#define HAKIOS_RPMPD_LEVEL_NOM        256u
#define HAKIOS_RPMPD_LEVEL_NOM_L1     320u
#define HAKIOS_RPMPD_LEVEL_TURBO      384u

uint32_t hakios_cmd_db_lookup(const char *name, const uint8_t **data_out, uint32_t *len_out);
bool hakios_rpmh_tcs_send(const uint32_t *addrs, const uint32_t *vals, unsigned count);
bool hakios_rpmh_regulator(const char *name, uint32_t mv, bool enable, uint32_t mode);
bool hakios_rpmh_arc_vote(const char *name, uint32_t min_level);

#endif /* KERNEL */

#endif /* _SYS_HAKIOS_RPMH_H_ */
