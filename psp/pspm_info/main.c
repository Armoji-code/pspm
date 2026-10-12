/* PSPM Info: writes everything the PSP can report to PSP/COMMON/pspm_info.txt for PSPM to read over USB. */
#include <pspkernel.h>
#include <pspdebug.h>
#include <pspctrl.h>
#include <psppower.h>
#include <psputility.h>
#include <psprtc.h>
#include <pspumd.h>
#include <psphprm.h>
#include <pspwlan.h>
#include <pspopenpsid.h>
#include <kubridge.h> /* kuKernelGetModel comes from the CFW's KUBridge module (ARK, PRO, ME) */
#include <systemctrl.h>
#include <systemctrl_se.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

PSP_MODULE_INFO("PSPM_INFO", 0, 1, 0);
PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER);

static FILE *out;

/* One key=value line, to the file and to the screen. */
static void put(const char *key, const char *fmt, ...) {
    char v[160];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(v, sizeof v, fmt, ap);
    va_end(ap);
    fprintf(out, "%s=%s\n", key, v);
    pspDebugScreenPrintf("%-16s %s\n", key, v);
}

static const char *yes(int b) { return b > 0 ? "yes" : "no"; }

static int param(int id) {
    int v = -1;
    sceUtilityGetSystemParamInt(id, &v);
    return v;
}

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
    out = fopen(path, "w");
    if (!out) {
        pspDebugScreenPrintf("Could not write %s\n", path);
        sceKernelDelayThread(4 * 1000 * 1000);
        sceKernelExitGame();
        return 0;
    }

    /* Hardware and firmware */
    unsigned fw = sceKernelDevkitVersion(); /* 0x06060110 = 6.61 */
    put("model", "%02dg", kuKernelGetModel() + 1);
    put("firmware", "%u.%u%u", fw >> 24, (fw >> 16) & 0xff, (fw >> 8) & 0xff);
    put("cfw_hen", "0x%x", sctrlHENGetVersion());
    put("cfw_hen_minor", "%d", sctrlHENGetMinorVersion());
    put("cfw_se", "0x%08x", sctrlSEGetVersion());
    put("toolkit", "%d", sctrlHENIsToolKit());
    PspOpenPSID psid;
    if (sceOpenPSIDGetOpenPSID(&psid) == 0) {
        char hex[33];
        for (int i = 0; i < 16; i++)
            sprintf(hex + 2 * i, "%02x", psid.data[i]);
        put("open_psid", "%s", hex);
    }
    put("cpu_clock", "%d MHz", scePowerGetCpuClockFrequency());
    put("bus_clock", "%d MHz", scePowerGetBusClockFrequency());
    put("free_ram", "%u KB", (unsigned)sceKernelTotalFreeMemSize() / 1024);
    put("max_free_block", "%u KB", (unsigned)sceKernelMaxFreeMemSize() / 1024);
    SceUID ids[256];
    int count = 0;
    sceKernelGetModuleIdList(ids, sizeof ids, &count);
    put("modules_loaded", "%d", count);
    put("uptime", "%llu s", (unsigned long long)sceKernelGetSystemTimeWide() / 1000000);

    /* Battery and power */
    if (scePowerIsBatteryExist()) {
        put("battery", "%d%%", scePowerGetBatteryLifePercent());
        put("battery_left", "%d min", scePowerGetBatteryLifeTime());
        put("battery_charging", "%s", yes(scePowerIsBatteryCharging()));
        put("battery_low", "%s", yes(scePowerIsLowBattery()));
        put("battery_capacity", "%d / %d mAh", scePowerGetBatteryRemainCapacity(), scePowerGetBatteryFullCapacity());
        put("battery_voltage", "%.2f V", scePowerGetBatteryVolt() / 1000.0);
        put("battery_current", "%d mA", scePowerGetBatteryElec());
        put("battery_temp", "%d C", scePowerGetBatteryTemp());
    } else {
        put("battery", "none");
    }
    put("charger", "%s", scePowerIsPowerOnline() ? "connected" : "not connected");

    /* Network */
    unsigned char mac[8] = {0};
    if (sceWlanGetEtherAddr(mac) == 0)
        put("wifi_mac", "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    put("wifi_switch", "%s", sceWlanGetSwitchState() ? "on" : "off");
    char nets[160] = "";
    for (int i = 1; i <= 10; i++) {
        netData d;
        if (sceUtilityCheckNetParam(i) == 0 && sceUtilityGetNetParam(i, PSP_NETPARAM_SSID, &d) == 0 && d.asString[0]) {
            if (nets[0])
                strncat(nets, ", ", sizeof nets - strlen(nets) - 1);
            strncat(nets, d.asString, sizeof nets - strlen(nets) - 1);
        }
    }
    put("wifi_networks", "%s", nets[0] ? nets : "none");

    /* Accessories */
    put("umd_inserted", "%s", yes(sceUmdCheckMedium()));
    put("headphones", "%s", yes(sceHprmIsHeadphoneExist()));
    put("remote", "%s", yes(sceHprmIsRemoteExist()));
    put("microphone", "%s", yes(sceHprmIsMicrophoneExist()));

    /* System settings */
    char nick[128] = "";
    sceUtilityGetSystemParamString(PSP_SYSTEMPARAM_ID_STRING_NICKNAME, nick, sizeof nick);
    put("nickname", "%s", nick);
    put("language", "%d", param(PSP_SYSTEMPARAM_ID_INT_LANGUAGE));
    put("confirm_button", "%s", param(PSP_SYSTEMPARAM_ID_INT_BUTTON_SWAP) == 0 ? "circle" : "cross");
    int tz = param(PSP_SYSTEMPARAM_ID_INT_TIMEZONE);
    put("timezone", "UTC%+d:%02d", tz / 60, (tz < 0 ? -tz : tz) % 60);
    put("daylight_saving", "%s", yes(param(PSP_SYSTEMPARAM_ID_INT_DAYLIGHTSAVINGS)));
    put("wifi_power_save", "%s", yes(param(PSP_SYSTEMPARAM_ID_INT_WLAN_POWERSAVE)));
    put("adhoc_channel", "%d", param(PSP_SYSTEMPARAM_ID_INT_ADHOC_CHANNEL));
    ScePspDateTime t;
    if (sceRtcGetCurrentClockLocalTime(&t) == 0)
        put("psp_clock", "%04d-%02d-%02d %02d:%02d:%02d", t.year, t.month, t.day, t.hour, t.minute, t.second);

    fclose(out);
    pspDebugScreenPrintf("\nSaved to %s\nPlug the PSP into your computer to see it in PSPM.\n\nPress X to exit.\n", path);
    SceCtrlData pad;
    do {
        sceCtrlReadBufferPositive(&pad, 1);
    } while (!(pad.Buttons & PSP_CTRL_CROSS));
    sceKernelExitGame();
    return 0;
}
