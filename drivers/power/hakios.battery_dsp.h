#ifndef BATT_DSP_H
#define BATT_DSP_H
#include <kern/locks.h>
#include <mach/mach_types.h>
extern lck_mtx_t batt_dsp_aop_mailbox_lock;
extern lck_mtx_t batt_dsp_smem_alloc_lock;
extern lck_mtx_t batt_dsp_rpmh_lock;
uint32_t batt_dsp_ipcc_take(vm_offset_t base, uint32_t client);
void batt_dsp_adsp_init(void);
void batt_dsp_adsp_stop(void);
void batt_dsp_adsp_quiesce(void);
const uint8_t *batt_dsp_image_file(const char *, const char *, uint64_t *);
#endif
