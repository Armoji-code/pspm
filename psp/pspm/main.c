/* PSPM, PSPM's own PSP app. SCREEN mode shows the PC's screen, which PSPM sends over the USB cable or Wi-Fi as 480x272 JPEG frames decoded
   by the PSP's hardware, and sends the PSP's buttons and stick back to the PC. CONTROLLER mode makes the PSP
   PPSSPP's gamepad; INFO mode writes the PSP's battery, firmware and model for PSPM (info.c). */
#include <pspkernel.h>
#include <pspctrl.h>
#include <pspdebug.h>
#include <pspdisplay.h>
#include <pspiofilemgr.h>
#include <pspjpeg.h>
#include <psppower.h>
#include <pspaudio.h>
#include <psputility.h>
#include <pspusb.h>
#include <pspusbstor.h>
#include <pspnet.h>
#include <pspnet_inet.h>
#include <pspnet_apctl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <kubridge.h> /* kuKernelLoadModule loads the kernel driver; from the CFW (ARK, PRO, ME) */
#include <stdio.h>
#include <string.h>

PSP_MODULE_INFO("PSPM", 0, 1, 2);
PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER);

int pspmUsbStart(void);
int pspmUsbRead(void *dst, int size);
int pspmUsbWrite(const void *src, int size);
unsigned pspmCtrlButtons(void);

#define W 480
#define H 272
#define STRIDE 512
#define HEAD 512 /* one USB packet before every frame: "PSPMSCR2", the JPEG's size (u32), a note to show (text) */
#define MAX_JPEG (256 * 1024)   /* PSPM keeps frames under this */
#define VRAM ((unsigned *)0x44000000) /* uncached */
#define BUFS (VRAM + STRIDE * H)      /* two 32-bit buffers after the debug screen's */

/* Picked in the menu, sent to the PC in every pad packet, kept in settings.bin next to the EBOOT. */
static const char *PRESETS[] = {"BEST QUALITY", "BALANCED", "BEST FPS"};
static volatile int preset = 1;

/* The note button cycles the PC's sound: off, on (22 kHz), best (44 kHz). Sent in every pad packet. */
static const char *AUDIO[] = {"AUDIO: OFF", "AUDIO: ON", "AUDIO: BEST"};
static volatile int audio = 0;
static volatile unsigned shown; /* until when (sceKernelGetSystemTimeLow) the PSP's own note shows */
static const char *note;

/* Sound from the PC ("PSPMAUD1", size, sample rate; 16-bit stereo PCM) waits here for the audio thread. */
#define RING (64 * 1024)
#define BLOCK 1024 /* stereo samples per output call */
static short ring[RING / 2];
static volatile unsigned head, tail; /* bytes written / played, free-running */
static volatile int rate;

/* Connection, picked in the menu: 0 = USB cable, else the PSP's saved Wi-Fi network with that number. Over Wi-Fi
   the PC finds the PSP with a UDP "PSPMFIND" (answered "PSPMHERE") and connects over TCP; the stream is the same. */
#define PORT 4000
#define FIND_PORT 4001
#define NETS 10
static int conn;
static char names[NETS + 1][32] = {"USB"};
static volatile int pc = -1; /* the PC's socket over Wi-Fi */

/* Mode, picked in the menu: the PC's screen, or controller for PPSSPP: the memory stick goes to the PC as a USB
   drive (so PPSSPP plays its games and saves) and the buttons go over Wi-Fi, sent as preset 3. */
static const char *MODES[] = {"SCREEN", "CONTROLLER (PPSSPP)", "INFO (FOR PSPM)"};
void info(const char *app);
static int mode;

static int rx(void *dst, int size) {
    if (!conn)
        return pspmUsbRead(dst, size);
    for (int got = 0; got < size;) {
        int n = recv(pc, (char *)dst + got, size - got, 0);
        if (n <= 0)
            return -1;
        got += n;
    }
    return size;
}

static int tx(const void *src, int size) {
    if (!conn)
        return pspmUsbWrite(src, size);
    return pc < 0 ? -1 : send(pc, src, size, 0);
}

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

/* Buttons and stick to the PC, 60 times a second: "PSPMPAD1", buttons (u32), stick x, y (0-255, 128 = middle),
   quality preset (0 best quality, 1 balanced, 2 best FPS), audio (0 off, 1 on, 2 best). */
static int pad(SceSize args, void *argp) {
    unsigned char msg[16] __attribute__((aligned(64))) = "PSPMPAD1";
    unsigned was = ~0u, ticks = 0;
    SceCtrlData c;
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_ANALOG);
    for (;;) {
        sceCtrlReadBufferPositive(&c, 1); /* waits for the next vblank */
        memcpy(msg + 8, &c.Buttons, 4);
        msg[12] = c.Lx;
        msg[13] = c.Ly;
        msg[14] = mode ? 3 : preset;
        unsigned all = pspmCtrlButtons(), now = all & ~was;
        was = all;
        if (now & PSP_CTRL_NOTE) {
            audio = (audio + 1) % 3;
            note = AUDIO[audio];
            shown = sceKernelGetSystemTimeLow() + 2000000;
        }
        msg[15] = audio;
        if (mode && !(++ticks % 60))
            scePowerTick(0); /* nothing else keeps the PSP awake in controller mode */
        if (tx(msg, sizeof msg) < 0)
            sceKernelDelayThread(100 * 1000); /* PC not reading yet */
    }
    return 0;
}

/* Plays what the PC sends; silence while nothing is queued, so a late packet only drops sound. */
static int sound(SceSize args, void *argp) {
    static short block[BLOCK * 2] __attribute__((aligned(64)));
    int ch = -1, at = 0;
    for (;;) {
        if (!audio || !rate) {
            if (ch >= 0)
                sceAudioSRCChRelease();
            ch = -1;
            tail = head;
            sceKernelDelayThread(50 * 1000);
            continue;
        }
        if (ch < 0 || at != rate) {
            if (ch >= 0)
                sceAudioSRCChRelease();
            at = rate;
            ch = sceAudioSRCChReserve(BLOCK, at, 2);
            if (ch < 0) {
                sceKernelDelayThread(100 * 1000);
                continue;
            }
        }
        if (head - tail >= sizeof block) {
            for (unsigned i = 0; i < sizeof block; i += 2)
                block[i / 2] = ring[((tail + i) % RING) / 2];
            tail += sizeof block;
        } else
            memset(block, 0, sizeof block);
        sceAudioSRCOutputBlocking(PSP_AUDIO_VOLUME_MAX, block);
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

/* Answers the PC's "PSPMFIND" broadcasts so it can find the PSP's address. */
static int find(SceSize args, void *argp) {
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(FIND_PORT)};
    bind(s, (struct sockaddr *)&a, sizeof a);
    for (;;) {
        char b[16];
        socklen_t n = sizeof a;
        if (recvfrom(s, b, sizeof b, 0, (struct sockaddr *)&a, &n) >= 8 && !memcmp(b, "PSPMFIND", 8))
            sendto(s, "PSPMHERE", 8, 0, (struct sockaddr *)&a, n);
    }
    return 0;
}

/* Joins the saved network, shows the PSP's address and listens for PSPM. */
static int wifi(void) {
    int r = sceUtilityLoadNetModule(PSP_NET_MODULE_COMMON);
    if (r >= 0)
        r = sceUtilityLoadNetModule(PSP_NET_MODULE_INET);
    if (r >= 0)
        r = sceNetInit(128 * 1024, 42, 4 * 1024, 42, 4 * 1024);
    if (r >= 0)
        r = sceNetInetInit();
    if (r >= 0)
        r = sceNetApctlInit(0x8000, 48);
    if (r >= 0)
        r = sceNetApctlConnect(conn);
    if (r < 0)
        stop("Could not start Wi-Fi (%08x).\nIs the WLAN switch on?", r);
    pspDebugScreenPrintf("Joining %s...\n", names[conn]);
    int state, was = 0;
    for (;;) {
        if (sceNetApctlGetState(&state) < 0 || (was && !state))
            stop("Could not join the network (%d).\nCheck it in SETTINGS > NETWORK SETTINGS.", was);
        if (state == PSP_NET_APCTL_STATE_GOT_IP)
            break;
        was = state;
        sceKernelDelayThread(50 * 1000);
    }
    SceNetApctlInfo info;
    sceNetApctlGetInfo(PSP_NET_APCTL_INFO_IP, &info);
    pspDebugScreenPrintf("Wi-Fi address: %s\n\n", info.ip);
    int s = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in a = {.sin_family = AF_INET, .sin_port = htons(PORT)};
    if (s < 0 || bind(s, (struct sockaddr *)&a, sizeof a) < 0 || listen(s, 1) < 0)
        stop("Could not listen for the PC (%08x).", s);
    start("find", find, 0x18);
    return s;
}

/* Shares the memory stick with the PC like USB mode does, while this keeps running from memory. */
static void storage(void) {
    static const char *mods[] = {"chkreg", "npdrm", "semawm", "usbstor", "usbstormgr", "usbstorms", "usbstorboot"};
    char path[64];
    for (int i = 0; i < sizeof mods / sizeof *mods; i++) {
        snprintf(path, sizeof path, "flash0:/kd/%s.prx", mods[i]);
        load(path); /* an error here means it already is */
    }
    int r = sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, 0);
    if (r >= 0)
        r = sceUsbStart(PSP_USBSTOR_DRIVERNAME, 0, 0);
    if (r >= 0)
        r = sceUsbstorBootSetCapacity(0x800000);
    if (r >= 0)
        r = sceUsbActivate(0x1c8);
    if (r < 0)
        stop("Could not share the memory stick over USB (%08x).", r);
}

/* Before streaming: UP/DOWN picks a row, LEFT/RIGHT changes it, CROSS starts. */
static void menu(const char *dir) {
    char file[256];
    unsigned char b[3];
    int count = 1, ids[NETS + 1] = {0}, row = 0, at = 0, rows[3], n;
    for (int i = 1; i <= NETS; i++) { /* the networks saved in SETTINGS > NETWORK SETTINGS */
        netData d;
        if (sceUtilityCheckNetParam(i) == 0 && sceUtilityGetNetParam(i, PSP_NETPARAM_NAME, &d) == 0) {
            snprintf(names[count], sizeof names[0], "WI-FI: %.24s", d.asString);
            ids[count++] = i;
        }
    }
    snprintf(file, sizeof file, "%ssettings.bin", dir);
    SceUID f = sceIoOpen(file, PSP_O_RDONLY, 0);
    if (f >= 0) {
        if (sceIoRead(f, b, 3) == 3) {
            preset = b[0] < 3 ? b[0] : preset;
            mode = b[2] < 3 ? b[2] : 0;
            for (int i = 0; i < count; i++)
                at = ids[i] == b[1] ? i : at;
        }
        sceIoClose(f);
    }
    SceCtrlData c;
    unsigned old = ~0u; /* nothing counts until it's released once */
    for (;;) {
        n = 0; /* the rows this mode uses: SCREEN all three, CONTROLLER no quality, INFO only the mode */
        rows[n++] = 0;
        if (mode == 0)
            rows[n++] = 1;
        if (mode < 2)
            rows[n++] = 2;
        row = row < n ? row : n - 1;
        pspDebugScreenSetXY(0, 2);
        for (int i = 0; i < 3; i++) {
            const char *label[] = {"MODE", "QUALITY", "CONNECTION"};
            const char *value[] = {MODES[mode], PRESETS[preset], names[at]};
            if (i < n)
                pspDebugScreenPrintf("%c %-12s < %-30s >\n\n", row == i ? '>' : ' ', label[rows[i]], value[rows[i]]);
            else
                pspDebugScreenPrintf("%-50s\n\n", "");
        }
        pspDebugScreenPrintf("\nUP/DOWN: PICK   LEFT/RIGHT: CHANGE   CROSS: START   HOME: QUIT");
        sceCtrlReadBufferPositive(&c, 1);
        unsigned now = c.Buttons & ~old;
        int step = (now & PSP_CTRL_RIGHT ? 1 : 0) - (now & PSP_CTRL_LEFT ? 1 : 0);
        old = c.Buttons;
        if (now & PSP_CTRL_DOWN)
            row = (row + 1) % n;
        if (now & PSP_CTRL_UP)
            row = (row + n - 1) % n;
        if (rows[row] == 0)
            mode = (mode + step + 3) % 3;
        else if (rows[row] == 1)
            preset = (preset + step + 3) % 3;
        else
            at = (at + step + count) % count;
        if (now & PSP_CTRL_CROSS)
            break;
    }
    conn = ids[at];
    b[0] = preset;
    b[1] = conn;
    b[2] = mode;
    f = sceIoOpen(file, PSP_O_WRONLY | PSP_O_CREAT | PSP_O_TRUNC, 0777);
    if (f >= 0) {
        sceIoWrite(f, b, 3);
        sceIoClose(f);
    }
    pspDebugScreenClear();
    if (mode == 0)
        pspDebugScreenPrintf("PSPM - %s - %s\n\n", PRESETS[preset], names[at]);
    else
        pspDebugScreenPrintf("PSPM - %s%s%s\n\n", MODES[mode], mode == 1 ? " - " : "", mode == 1 ? names[at] : "");
}

int main(int argc, char *argv[]) {
    start("callbacks", callbacks, 0x11);
    pspDebugScreenInit();
    pspDebugScreenPrintf("PSPM\n\n");

    load("flash0:/kd/usb.prx"); /* not loaded in every mode; an error here means it already is */
    char path[256] = "ms0:/PSP/GAME/PSPM/";
    if (argc > 0 && strrchr(argv[0], '/') && strlen(argv[0]) < sizeof path - 16) {
        strcpy(path, argv[0]);
        strrchr(path, '/')[1] = 0;
    }
    char dir[256];
    strcpy(dir, path);
    strcat(path, "pspm_usb.prx");
    SceUID m = load(path);
    if (m < 0)
        stop("Could not load pspm_usb.prx (%08x).\nInstall the PSP app again from PSPM.", m);
    int r = sceUtilityLoadModule(PSP_MODULE_AV_AVCODEC);
    if (r >= 0)
        r = sceJpegInitMJpeg();
    if (r >= 0)
        r = sceJpegCreateMJpeg(W, H);
    if (r < 0)
        stop("Could not start the JPEG decoder (%08x).", r);
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_ANALOG);
    menu(dir);
    if (mode == 2) {
        info(dir);
        pspDebugScreenPrintf("\nPress HOME to quit.");
        sceKernelSleepThread();
    }
    if (mode && !conn)
        stop("Controller mode sends the buttons over Wi-Fi:\npick a Wi-Fi network in CONNECTION.", 0);
    int server = conn ? wifi() : -1;
    if (mode)
        storage();
    r = conn ? 0 : pspmUsbStart();
    if (r < 0)
        stop("Could not start USB (%08x).\nIf RemoteJoyLite is on for games, turn it off\n"
             "(PSPM: STREAMING > REMOVE PLUGIN) and restart the PSP.", r);
    start("pad", pad, 0x12);
    if (mode)
        pspDebugScreenPrintf("The memory stick is on the PC now; start your game in PPSSPP.\n"
                             "In PSPM: STREAMING > START SENDING makes this PSP its controller.\n\nHOME quits.");
    else {
        start("sound", sound, 0x10);
        pspDebugScreenPrintf("Waiting for the PC.\nIn PSPM: STREAMING > START SENDING.\n\nHOME quits.");
    }

    int back = 0;
    for (;;) {
        if (conn && pc < 0) {
            pc = accept(server, NULL, NULL);
            continue;
        }
        if (rx(in, HEAD) != HEAD) {
            if (conn) { /* the PC stopped: wait for the next one */
                close(pc);
                pc = -1;
            } else
                sceKernelDelayThread(100 * 1000);
            continue;
        }
        if (mode)
            continue; /* controller: the PC's stream isn't shown */
        unsigned size;
        memcpy(&size, in + 8, 4);
        if (!memcmp(in, "PSPMAUD1", 8)) {
            if (size > MAX_JPEG || rx(in + HEAD, (size + 511) & ~511) < 0)
                continue;
            memcpy((void *)&rate, in + 12, 4);
            if (head - tail + size > RING / 2) /* falling behind: drop the oldest instead of adding delay */
                tail = head + size - RING / 4;
            for (unsigned i = 0; i < size; i += 2)
                ring[((head + i) % RING) / 2] = *(short *)(in + HEAD + i);
            head += size & ~3u;
            continue;
        }
        if (memcmp(in, "PSPMSCR2", 8))
            continue; /* joined mid-frame: skip packets until the next header */
        if (size > MAX_JPEG || rx(in + HEAD, (size + 511) & ~511) < 0)
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
        in[HEAD - 1] = 0;
        const char *say = in[12] ? (char *)in + 12 : /* a note from PSPM, e.g. the profile SELECT switched to */
                          (int)(shown - sceKernelGetSystemTimeLow()) > 0 ? note : NULL;
        if (say) {
            pspDebugScreenSetBase((u32 *)dst);
            pspDebugScreenSetXY(1, 1);
            pspDebugScreenPrintf("%s", say);
        }
        sceDisplaySetFrameBuf(dst, STRIDE, PSP_DISPLAY_PIXEL_FORMAT_8888, PSP_DISPLAY_SETBUF_NEXTFRAME);
        back ^= 1;
        scePowerTick(0); /* the PC is the input now: don't dim or sleep */
    }
    return 0;
}
