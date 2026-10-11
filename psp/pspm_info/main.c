/* PSPM Info: writes model, firmware and battery to PSP/COMMON/pspm_info.txt for PSPM to read over USB. */
#include <pspkernel.h>
#include <pspdebug.h>
#include <psppower.h>
#include <stdio.h>
#include <string.h>

PSP_MODULE_INFO("PSPM_INFO", 0, 1, 0);
PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER);

int sceKernelDevkitVersion(void);
int sceKernelGetModel(void);

int main(int argc, char *argv[]) {
    /* PSP Go keeps apps on ef0:, so write to the device we were started from. */
    char dir[64] = "ms0:";
    const char *colon = argc > 0 ? strchr(argv[0], ':') : NULL;
    if (colon && colon - argv[0] < 8)
        snprintf(dir, sizeof dir, "%.*s", (int)(colon - argv[0] + 1), argv[0]);
    char path[96];
    snprintf(path, sizeof path, "%s/PSP/COMMON", dir);
    sceIoMkdir(path, 0777);
    strcat(path, "/pspm_info.txt");

    pspDebugScreenInit();
    FILE *f = fopen(path, "w");
    if (!f) {
        pspDebugScreenPrintf("Could not write %s\n", path);
    } else {
        unsigned fw = sceKernelDevkitVersion(); /* 0x06060110 = 6.61 */
        fprintf(f, "model=%02dg\n", sceKernelGetModel() + 1);
        fprintf(f, "firmware=%u.%u%u\n", fw >> 24, (fw >> 16) & 0xff, (fw >> 8) & 0xff);
        if (scePowerIsBatteryExist()) {
            fprintf(f, "battery=%d%%\n", scePowerGetBatteryLifePercent());
            fprintf(f, "battery_left=%d min\n", scePowerGetBatteryLifeTime());
            fprintf(f, "battery_voltage=%.2f V\n", scePowerGetBatteryVolt() / 1000.0);
            fprintf(f, "battery_temp=%d C\n", scePowerGetBatteryTemp());
        } else {
            fprintf(f, "battery=none\n");
        }
        fprintf(f, "charger=%s\n", scePowerIsPowerOnline() ? "connected" : "not connected");
        fclose(f);
        pspDebugScreenPrintf("Saved to %s\n\nPlug the PSP into your computer to see it in PSPM.\n", path);
    }
    sceKernelDelayThread(4 * 1000 * 1000);
    sceKernelExitGame();
    return 0;
}
