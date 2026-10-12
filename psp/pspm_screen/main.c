/* PSPM Screen: shows the PC's screen, which PSPM sends over the USB cable as 480x272 16-bit frames. */
#include <pspkernel.h>
#include <pspdebug.h>
#include <pspdisplay.h>
#include <psppower.h>
#include <kubridge.h> /* kuKernelLoadModule loads the kernel driver; from the CFW (ARK, PRO, ME) */
#include <stdio.h>
#include <string.h>

PSP_MODULE_INFO("PSPM_SCREEN", 0, 1, 0);
PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER);

int pspmUsbStart(void);
int pspmUsbRead(void *dst, int size);

#define W 480
#define H 272
#define STRIDE 512
#define HEAD 512 /* one USB packet: "PSPMSCR1" then zeros, before every frame */
#define FRAME (W * H * 2)
#define VRAM ((unsigned short *)0x44000000) /* uncached */
#define BUFS (VRAM + STRIDE * H * 2)        /* two 16-bit buffers after the debug screen's 32-bit one */

static unsigned char in[HEAD + FRAME] __attribute__((aligned(64)));

static int on_exit(int a, int b, void *c) {
    sceKernelExitGame();
    return 0;
}

static int callbacks(SceSize args, void *argp) {
    sceKernelRegisterExitCallback(sceKernelCreateCallback("exit", on_exit, NULL));
    sceKernelSleepThreadCB();
    return 0;
}

static SceUID load(const char *path) {
    SceUID m = kuKernelLoadModule(path, 0, NULL);
    if (m >= 0)
        sceKernelStartModule(m, 0, NULL, NULL, NULL);
    return m;
}

static void stop(const char *fmt, int err) {
    pspDebugScreenPrintf(fmt, err);
    pspDebugScreenPrintf("\n\nPress HOME to quit.");
    sceKernelSleepThread();
}

int main(int argc, char *argv[]) {
    SceUID t = sceKernelCreateThread("callbacks", callbacks, 0x11, 0xFA0, 0, NULL);
    if (t >= 0)
        sceKernelStartThread(t, 0, NULL);
    pspDebugScreenInit();
    pspDebugScreenPrintf("PSPM SCREEN\n\n");

    load("flash0:/kd/usb.prx"); /* not loaded in every mode; an error here means it already is */
    char path[256] = "ms0:/PSP/GAME/PSPM_SCREEN/";
    if (argc > 0 && strrchr(argv[0], '/') && strlen(argv[0]) < sizeof path - 16) {
        strcpy(path, argv[0]);
        strrchr(path, '/')[1] = 0;
    }
    strcat(path, "pspm_usb.prx");
    SceUID m = load(path);
    if (m < 0)
        stop("Could not load pspm_usb.prx (%08x).\nInstall PSPM Screen again from PSPM.", m);
    int r = pspmUsbStart();
    if (r < 0)
        stop("Could not start USB (%08x).\nIf RemoteJoyLite is on for games, turn it off\n"
             "(PSPM: STREAMING > REMOVE PLUGIN) and restart the PSP.", r);
    pspDebugScreenPrintf("Waiting for the PC.\nIn PSPM: STREAMING > START SENDING.\n\nHOME quits.");

    int back = 0;
    for (;;) {
        if (pspmUsbRead(in, HEAD) != HEAD) {
            sceKernelDelayThread(100 * 1000);
            continue;
        }
        if (memcmp(in, "PSPMSCR1", 8))
            continue; /* joined mid-frame: skip packets until the next header */
        if (pspmUsbRead(in + HEAD, FRAME) != FRAME)
            continue;
        unsigned short *dst = BUFS + back * STRIDE * H;
        for (int y = 0; y < H; y++)
            memcpy(dst + y * STRIDE, in + HEAD + y * W * 2, W * 2);
        sceDisplaySetFrameBuf(dst, STRIDE, PSP_DISPLAY_PIXEL_FORMAT_565, PSP_DISPLAY_SETBUF_NEXTFRAME);
        back ^= 1;
        scePowerTick(0); /* the PC is the input now: don't dim or sleep */
    }
    return 0;
}
