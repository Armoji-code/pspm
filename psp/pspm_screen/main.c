/* PSPM Screen: shows the PC's screen, which PSPM sends over the USB cable as 480x272 JPEG frames decoded by the
   PSP's hardware, and sends the PSP's buttons and stick back to the PC. */
#include <pspkernel.h>
#include <pspctrl.h>
#include <pspdebug.h>
#include <pspdisplay.h>
#include <pspjpeg.h>
#include <psppower.h>
#include <psputility.h>
#include <kubridge.h> /* kuKernelLoadModule loads the kernel driver; from the CFW (ARK, PRO, ME) */
#include <stdio.h>
#include <string.h>

PSP_MODULE_INFO("PSPM_SCREEN", 0, 1, 1);
PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER);

int pspmUsbStart(void);
int pspmUsbRead(void *dst, int size);
int pspmUsbWrite(const void *src, int size);

#define W 480
#define H 272
#define STRIDE 512
#define HEAD 512                /* one USB packet before every frame: "PSPMSCR2", then the JPEG's size (u32) */
#define MAX_JPEG (256 * 1024)   /* PSPM keeps frames under this */
#define VRAM ((unsigned *)0x44000000) /* uncached */
#define BUFS (VRAM + STRIDE * H)      /* two 32-bit buffers after the debug screen's */

static unsigned char in[HEAD + MAX_JPEG] __attribute__((aligned(64)));
static unsigned out[W * H] __attribute__((aligned(64)));

static int on_exit(int a, int b, void *c) {
    sceKernelExitGame();
    return 0;
}

static int callbacks(SceSize args, void *argp) {
    sceKernelRegisterExitCallback(sceKernelCreateCallback("exit", on_exit, NULL));
    sceKernelSleepThreadCB();
    return 0;
}

/* Buttons and stick to the PC, 60 times a second: "PSPMPAD1", buttons (u32), stick x, y (0-255, 128 = middle). */
static int pad(SceSize args, void *argp) {
    unsigned char msg[16] __attribute__((aligned(64))) = "PSPMPAD1";
    SceCtrlData c;
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_ANALOG);
    for (;;) {
        sceCtrlReadBufferPositive(&c, 1); /* waits for the next vblank */
        memcpy(msg + 8, &c.Buttons, 4);
        msg[12] = c.Lx;
        msg[13] = c.Ly;
        if (pspmUsbWrite(msg, sizeof msg) < 0)
            sceKernelDelayThread(100 * 1000); /* PC not reading yet */
    }
    return 0;
}

static void start(const char *name, SceKernelThreadEntry fn, int prio) {
    SceUID t = sceKernelCreateThread(name, fn, prio, 0x1000, 0, NULL);
    if (t >= 0)
        sceKernelStartThread(t, 0, NULL);
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
    start("callbacks", callbacks, 0x11);
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
    int r = sceUtilityLoadModule(PSP_MODULE_AV_AVCODEC);
    if (r >= 0)
        r = sceJpegInitMJpeg();
    if (r >= 0)
        r = sceJpegCreateMJpeg(W, H);
    if (r < 0)
        stop("Could not start the JPEG decoder (%08x).", r);
    r = pspmUsbStart();
    if (r < 0)
        stop("Could not start USB (%08x).\nIf RemoteJoyLite is on for games, turn it off\n"
             "(PSPM: STREAMING > REMOVE PLUGIN) and restart the PSP.", r);
    start("pad", pad, 0x12);
    pspDebugScreenPrintf("Waiting for the PC.\nIn PSPM: STREAMING > START SENDING.\n\nHOME quits.");

    int back = 0;
    for (;;) {
        if (pspmUsbRead(in, HEAD) != HEAD) {
            sceKernelDelayThread(100 * 1000);
            continue;
        }
        if (memcmp(in, "PSPMSCR2", 8))
            continue; /* joined mid-frame: skip packets until the next header */
        unsigned size;
        memcpy(&size, in + 8, 4);
        if (size > MAX_JPEG || pspmUsbRead(in + HEAD, (size + 511) & ~511) < 0)
            continue;
        /* the decoder reads and writes memory directly, past the CPU cache */
        sceKernelDcacheWritebackInvalidateRange(in + HEAD, size);
        sceKernelDcacheWritebackInvalidateRange(out, sizeof out);
        if (sceJpegDecodeMJpeg(in + HEAD, size, (u8 *)out, 0) < 0)
            continue;
        sceKernelDcacheWritebackInvalidateRange(out, sizeof out);
        unsigned *dst = BUFS + back * STRIDE * H;
        for (int y = 0; y < H; y++)
            memcpy(dst + y * STRIDE, out + y * W, W * 4);
        sceDisplaySetFrameBuf(dst, STRIDE, PSP_DISPLAY_PIXEL_FORMAT_8888, PSP_DISPLAY_SETBUF_NEXTFRAME);
        back ^= 1;
        scePowerTick(0); /* the PC is the input now: don't dim or sleep */
    }
    return 0;
}
