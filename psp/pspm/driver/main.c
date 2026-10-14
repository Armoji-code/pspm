/* PSPM's USB driver. Two devices, one at a time:
   - the vendor device (SCREEN mode): bulk endpoints the PC writes frames to and reads the buttons from;
   - CONTROLLER mode over the cable: one device with two parts, a read-only USB drive of the memory stick (so
     PPSSPP plays its games) and a plain USB gamepad (HID, no driver needed on any OS, like FuSa GamePad).
   Kernel mode, since only kernel code can register a USB driver; the app calls in through syscalls. */
#include <pspkernel.h>
#include <pspsdk.h>
#include <pspusb.h>
#include <pspusbbus.h>
#include <pspctrl.h>
#include <pspiofilemgr.h>
#include <string.h>

PSP_MODULE_INFO("pspm_usb", PSP_MODULE_KERNEL, 1, 1);

#define DRIVER "PSPMScreenDriver"
#define DRIVE "PSPMDriveDriver"
#define PID 0x1c9 /* PSPLink's and RemoteJoyLite's "Type B": their udev rule and Windows driver already cover it */

/* Same layout as PSPLink/RemoteJoyLite: ep1 bulk in, ep2 and ep3 bulk out. Frames come on ep2, buttons go on ep1. */
static struct UsbEndpoint endp[4] = {{0, 0, 0}, {1, 0, 0}, {2, 0, 0}, {3, 0, 0}};
static struct UsbInterface intf = {0xFFFFFFFF, 0, 1};
static struct UsbData data[2]; /* [0] high speed, [1] full speed */
static struct StringDescriptor str = {6, 3, {'<', '>', 0}};

static struct DeviceDescriptor dev = {
    .bLength = 18, .bDescriptorType = 1, .bcdUSB = 0x200, .bMaxPacketSize = 64,
    .bcdDevice = 0x200, /* PSPM finds this device, not RemoteJoyLite's (0x100), by this */
    .bNumConfigurations = 1};
static struct ConfigDescriptor conf = {
    .bLength = 9, .bDescriptorType = 2, .wTotalLength = 9 + 9 + 3 * 7, .bNumInterfaces = 1,
    .bConfigurationValue = 1, .bmAttributes = 0xC0};
static struct InterfaceDescriptor iface = {
    .bLength = 9, .bDescriptorType = 4, .bNumEndpoints = 3, .bInterfaceClass = 0xFF, .bInterfaceSubClass = 1,
    .bInterfaceProtocol = 0xFF, .iInterface = 1};
static void fill(struct UsbData *d, int packet) {
    static const unsigned char addr[3] = {0x81, 0x02, 0x03};
    memcpy(d->devdesc, &dev, sizeof dev);
    d->config.pconfdesc = &d->confdesc;
    d->config.pinterfaces = &d->interfaces;
    d->config.pinterdesc = &d->interdesc;
    d->config.pendp = &d->endp[0];
    memcpy(d->confdesc.desc, &conf, sizeof conf);
    d->confdesc.pinterfaces = &d->interfaces;
    d->interfaces.pinterdesc[0] = &d->interdesc;
    d->interfaces.intcount = 1;
    memcpy(d->interdesc.desc, &iface, sizeof iface);
    d->interdesc.pendp = &d->endp[0];
    for (int i = 0; i < 3; i++) {
        struct EndpointDescriptor e = {7, 5, addr[i], 2, packet, 0};
        memcpy(d->endp[i].desc, &e, sizeof e);
    }
}

static int none(void) { return 0; }

static int start_func(int size, void *args) {
    memset(data, 0, sizeof data);
    fill(&data[0], 512);
    fill(&data[1], 64);
    return 0;
}

/* CONTROLLER mode: interface 0 the drive (ep1 bulk in, ep2 bulk out, like the PSP's own USB mode), interface 1
   the gamepad (ep3 interrupt in). The SDK's UsbData only holds one interface, so these are spelled out in the
   layout the bus driver reads: each descriptor followed by pointers to what comes under it. */
struct epd { unsigned char d[8]; const void *extra; int len; };
struct ifd { unsigned char d[12]; struct epd *ep; const void *extra; int len; };
struct sets { struct ifd *ifd; unsigned alt, n; };
struct cfd { unsigned char d[12]; struct sets *s; const void *extra; int len; };
struct conf { struct cfd *c; struct sets *s; struct ifd *i; struct epd *e; };

/* The gamepad's 5-byte report: X, Y, the d-pad as a hat (8 = centred), 12 buttons. */
static const unsigned char report_desc[] __attribute__((aligned(64))) = {
    0x05, 0x01, 0x09, 0x05, 0xA1, 0x01,                         /* generic desktop, gamepad, application */
    0x09, 0x30, 0x09, 0x31, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x02, 0x81, 0x02, /* X, Y: 0..255 */
    0x09, 0x39, 0x15, 0x00, 0x25, 0x07, 0x35, 0x00, 0x46, 0x3B, 0x01, 0x65, 0x14,             /* hat: 0..7, */
    0x75, 0x04, 0x95, 0x01, 0x81, 0x42, 0x65, 0x00, 0x81, 0x03,                               /* 8 = none */
    0x05, 0x09, 0x19, 0x01, 0x29, 0x0C, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x0C, 0x81, 0x02, /* buttons */
    0x75, 0x04, 0x95, 0x01, 0x81, 0x03, 0xC0};
static const unsigned char hid_desc[9] = {9, 0x21, 0x11, 0x01, 0, 1, 0x22, sizeof report_desc, 0};

static struct UsbEndpoint dendp[4] = {{0, 0, 0}, {1, 0, 0}, {2, 0, 0}, {3, 0, 0}};
static struct UsbInterface dintf = {0xFFFFFFFF, 0, 2};
static struct DeviceDescriptor ddev[2] __attribute__((aligned(16))); /* [0] high speed, [1] full speed */
static struct epd depd[2][3] __attribute__((aligned(16)));
static struct ifd difd[2][2];
static struct sets dsets[2][2];
static struct cfd dcfd[2];
static struct conf dconf[2];

static int drive_start(int size, void *args) {
    for (int s = 0; s < 2; s++) {
        int bulk = s ? 64 : 512;
        unsigned char e[3][7] = {{7, 5, 0x81, 2, bulk, bulk >> 8, 0}, {7, 5, 0x02, 2, bulk, bulk >> 8, 0},
                                 {7, 5, 0x83, 3, 8, 0, s ? 1 : 4}}; /* interrupt: every 1 ms */
        unsigned char i[2][9] = {{9, 4, 0, 0, 2, 8, 6, 0x50, 1},  /* mass storage, SCSI, bulk-only */
                                 {9, 4, 1, 0, 1, 3, 0, 0, 1}};    /* HID, no boot protocol */
        unsigned char c[9] = {9, 2, 9 + 9 + 2 * 7 + 9 + 9 + 7, 0, 2, 1, 0, 0xC0, 0};
        memset(depd[s], 0, sizeof depd[s]);
        for (int k = 0; k < 3; k++)
            memcpy(depd[s][k].d, e[k], 7);
        memset(difd[s], 0, sizeof difd[s]);
        for (int k = 0; k < 2; k++) {
            memcpy(difd[s][k].d, i[k], 9);
            difd[s][k].ep = &depd[s][k ? 2 : 0];
            dsets[s][k] = (struct sets){&difd[s][k], 0, 1};
        }
        difd[s][1].extra = hid_desc;
        difd[s][1].len = sizeof hid_desc;
        memset(&dcfd[s], 0, sizeof dcfd[s]);
        memcpy(dcfd[s].d, c, 9);
        dcfd[s].s = dsets[s];
        dconf[s] = (struct conf){&dcfd[s], dsets[s], difd[s], depd[s]};
        ddev[s] = dev;
        ddev[s].bcdUSB = s ? 0x110 : 0x200;
        ddev[s].bcdDevice = 0x300;
    }
    return 0;
}

/* Requests to the device: GET MAX LUN for the drive (one drive, LUN 0), the gamepad's report descriptor, and
   HID SET IDLE (fine, the gamepad sends anyway). Anything else is refused. */
static struct UsbdDeviceReq ctl;
static unsigned char reply[128] __attribute__((aligned(64)));

static int drive_ctl(int a, int b, struct DeviceRequest *r) {
    int n;
    if (r->bRequest == 0xFE)
        reply[0] = 0, n = 1;
    else if (r->bRequest == 6 && r->wValue == 0x2200)
        memcpy(reply, report_desc, sizeof report_desc), n = sizeof report_desc;
    else
        return r->bRequest == 0x0A && r->bmRequestType == 0x21 ? 0 : -1;
    memset(&ctl, 0, sizeof ctl);
    sceKernelDcacheWritebackRange(reply, sizeof reply);
    ctl.endp = &dendp[0];
    ctl.data = reply;
    ctl.size = n < r->wLength ? n : r->wLength;
    ctl.func = none;
    return sceUsbbdReqSend(&ctl) < 0 ? -1 : 0;
}

static struct UsbDriver driver = {
    DRIVER, 4, endp, &intf, &data[0].devdesc[0], &data[0].config, &data[1].devdesc[0], &data[1].config, &str,
    (void *)none, (void *)none, (void *)none, (void *)none, 0, start_func, (void *)none, NULL};
static struct UsbDriver drive_driver = {
    DRIVE, 4, dendp, &dintf, &ddev[0], &dconf[0], &ddev[1], &dconf[1], &str,
    drive_ctl, (void *)none, (void *)none, (void *)none, 0, drive_start, (void *)none, NULL};

/* One request, event flag and buffer per direction: frames come in on ep2 while buttons go out on ep1. */
struct pipe {
    struct UsbdDeviceReq req;
    SceUID flag;
    struct UsbEndpoint *ep;
    char *buf;
    int size;
};
static char in_buf[64 * 1024] __attribute__((aligned(64)));
static char out_buf[512] __attribute__((aligned(64)));
static char sect[32 * 1024] __attribute__((aligned(64))); /* sectors on their way to the PC */
static struct pipe pin = {.ep = &endp[2], .buf = in_buf, .size = sizeof in_buf};
static struct pipe pout = {.ep = &endp[1], .buf = out_buf, .size = sizeof out_buf};
static struct pipe din = {.ep = &dendp[2], .buf = in_buf, .size = sizeof in_buf};
static struct pipe dout = {.ep = &dendp[1], .buf = out_buf, .size = sizeof out_buf};
static struct pipe dsect = {.ep = &dendp[1], .buf = sect, .size = sizeof sect};
static char hid_buf[64] __attribute__((aligned(64)));
static struct pipe dhid = {.ep = &dendp[3], .buf = hid_buf, .size = sizeof hid_buf};
static int started, drive;

static int done(struct UsbdDeviceReq *r, int a, int b) {
    sceKernelSetEventFlag(((struct pipe *)r->arg)->flag, 1);
    return 0;
}

/* Queue one transfer of n bytes on p's endpoint and wait for it. Returns bytes moved, or < 0. */
static int transfer(struct pipe *p, int n, int send) {
    memset(&p->req, 0, sizeof p->req);
    p->req.endp = p->ep;
    p->req.data = p->buf;
    p->req.size = n;
    p->req.func = done;
    p->req.arg = p;
    sceKernelClearEventFlag(p->flag, 0);
    if (send)
        sceKernelDcacheWritebackRange(p->buf, n);
    else
        sceKernelDcacheInvalidateRange(p->buf, n);
    if ((send ? sceUsbbdReqSend(&p->req) : sceUsbbdReqRecv(&p->req)) < 0 ||
        sceKernelWaitEventFlag(p->flag, 1, PSP_EVENT_WAITOR | PSP_EVENT_WAITCLEAR, NULL, NULL) < 0 || p->req.retcode ||
        p->req.recvsize <= 0)
        return -1;
    return p->req.recvsize;
}

/* The drive: SCSI commands in bulk-only wrappers (CBW in, data, CSW out). Read-only: writes fail as write
   protected, so nothing on the PC can change the stick. */
static SceUID ms = -1;
static unsigned ms_sectors;
static unsigned char sense[3]; /* key, ASC, ASCQ of the last failure, for REQUEST SENSE */

static unsigned le32(const unsigned char *p) { return p[0] | p[1] << 8 | p[2] << 16 | (unsigned)p[3] << 24; }
static unsigned be32(const unsigned char *p) { return (unsigned)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static void put_be32(unsigned char *p, unsigned v) {
    p[0] = v >> 24, p[1] = v >> 16, p[2] = v >> 8, p[3] = v;
}

static int send_sectors(unsigned lba, unsigned count) {
    while (count) {
        unsigned n = count < sizeof sect / 512 ? count : sizeof sect / 512;
        if (sceIoLseek(ms, (SceOff)lba * 512, PSP_SEEK_SET) < 0 || sceIoRead(ms, sect, n * 512) != n * 512 ||
            transfer(&dsect, n * 512, 1) < 0)
            return -1;
        lba += n;
        count -= n;
    }
    return 0;
}

static void command(const unsigned char *cbw) {
    unsigned want = le32(cbw + 8), sent = 0;
    int in = cbw[12] & 0x80, fail = 0, n = 0;
    const unsigned char *cb = cbw + 15;
    unsigned char r[36] = {0};
    switch (cb[0]) {
    case 0x00: /* TEST UNIT READY */
    case 0x1B: /* START STOP UNIT */
    case 0x1E: /* PREVENT ALLOW MEDIUM REMOVAL */
    case 0x2F: /* VERIFY */
    case 0x35: /* SYNCHRONIZE CACHE */
        break;
    case 0x12: /* INQUIRY: removable disk */
        r[1] = 0x80, r[2] = 2, r[3] = 2, r[4] = 31;
        memcpy(r + 8, "Sony    " "PSPM read-only  " "1.00", 28); /* vendor, product, revision */
        n = 36;
        break;
    case 0x03: /* REQUEST SENSE */
        r[0] = 0x70, r[2] = sense[0], r[7] = 10, r[12] = sense[1], r[13] = sense[2];
        memset(sense, 0, sizeof sense);
        n = 18;
        break;
    case 0x1A: /* MODE SENSE (6): write protected */
        r[0] = 3, r[2] = 0x80;
        n = 4;
        break;
    case 0x5A: /* MODE SENSE (10): write protected */
        r[1] = 6, r[3] = 0x80;
        n = 8;
        break;
    case 0x25: /* READ CAPACITY (10): last sector, sector size */
        put_be32(r, ms_sectors - 1);
        put_be32(r + 4, 512);
        n = 8;
        break;
    case 0x28: { /* READ (10) */
        unsigned lba = be32(cb + 2), count = cb[7] << 8 | cb[8];
        if (lba + count > ms_sectors || count * 512 > want || send_sectors(lba, count) < 0)
            fail = 1, sense[0] = 3, sense[1] = 0x11; /* MEDIUM ERROR, unrecovered read error */
        else
            sent = count * 512;
        break;
    }
    case 0x2A: /* WRITE (10) */
        fail = 1, sense[0] = 7, sense[1] = 0x27; /* DATA PROTECT, write protected */
        break;
    default:
        fail = 1, sense[0] = 5, sense[1] = 0x20; /* ILLEGAL REQUEST, invalid command */
    }
    if (n && !fail) {
        n = (unsigned)n < want ? n : want;
        memcpy(out_buf, r, n);
        if (n && transfer(&dout, n, 1) > 0)
            sent = n;
    }
    if (want > sent && !in) /* the PC sends data we don't want (a write): take it so the next CBW lines up */
        for (unsigned left = want; left;) {
            int got = transfer(&din, left < din.size ? left : din.size, 0);
            if (got <= 0)
                break;
            left -= got < left ? got : left;
        }
    else if (want && !sent) { /* data expected but none to give: a short packet ends that stage */
        int z = want < 512 ? want : 511;
        memset(out_buf, 0, z);
        transfer(&dout, z, 1);
    }
    unsigned char *c = (unsigned char *)out_buf;
    memcpy(c, "USBS", 4);
    memcpy(c + 4, cbw + 4, 4);
    unsigned residue = want - sent;
    c[8] = residue, c[9] = residue >> 8, c[10] = residue >> 16, c[11] = residue >> 24;
    c[12] = fail;
    transfer(&dout, 13, 1);
}

static int drive_thread(SceSize args, void *argp) {
    unsigned char cbw[31];
    for (;;) {
        int n = transfer(&din, 512, 0);
        if (n < 0) {
            sceKernelDelayThread(100 * 1000); /* not plugged in yet */
            continue;
        }
        if (n == 31 && !memcmp(in_buf, "USBC", 4)) {
            memcpy(cbw, in_buf, 31);
            command(cbw);
        }
    }
    return 0;
}

/* The gamepad: the buttons and stick as a report every 4 ms (each waits for the PC to take the last one). */
static int hid_thread(SceSize args, void *argp) {
    static const unsigned buttons[12] = {PSP_CTRL_CROSS, PSP_CTRL_CIRCLE, PSP_CTRL_SQUARE, PSP_CTRL_TRIANGLE,
                                         PSP_CTRL_LTRIGGER, PSP_CTRL_RTRIGGER, PSP_CTRL_SELECT, PSP_CTRL_START,
                                         PSP_CTRL_NOTE, PSP_CTRL_VOLDOWN, PSP_CTRL_VOLUP, PSP_CTRL_SCREEN};
    static const unsigned char hat[9] = {7, 0, 1, 6, 8, 2, 5, 4, 3}; /* by (y + 1) * 3 + x + 1 */
    SceCtrlData c;
    for (;;) {
        sceCtrlPeekBufferPositive(&c, 1);
        int x = !!(c.Buttons & PSP_CTRL_RIGHT) - !!(c.Buttons & PSP_CTRL_LEFT);
        int y = !!(c.Buttons & PSP_CTRL_DOWN) - !!(c.Buttons & PSP_CTRL_UP);
        unsigned b = 0;
        for (int i = 0; i < 12; i++)
            b |= c.Buttons & buttons[i] ? 1u << i : 0;
        hid_buf[0] = c.Lx, hid_buf[1] = c.Ly, hid_buf[2] = hat[(y + 1) * 3 + x + 1], hid_buf[3] = b, hid_buf[4] = b >> 8;
        if (transfer(&dhid, 5, 1) < 0)
            sceKernelDelayThread(100 * 1000); /* not plugged in yet */
        sceKernelDelayThread(4000);
    }
    return 0;
}

/* Opens the memory stick for reading and finds its size: the whole device, else where its partition ends. */
static int open_stick(void) {
    ms = sceIoOpen("msstor0:", PSP_O_RDONLY, 0);
    if (ms < 0)
        return ms;
    SceOff end = sceIoLseek(ms, 0, PSP_SEEK_END);
    if (end > 0) {
        ms_sectors = end / 512;
        return 0;
    }
    if (sceIoLseek(ms, 0, PSP_SEEK_SET) < 0 || sceIoRead(ms, sect, 512) != 512)
        return -1;
    const unsigned char *part = (unsigned char *)sect + 0x1BE;
    ms_sectors = le32(part + 8) + le32(part + 12);
    return ms_sectors ? 0 : -1;
}

static int user_ptr(const void *p, int size) { /* a user pointer only, never kernel memory */
    return !(((unsigned)p | ((unsigned)p + size)) >> 31);
}

/* as_drive 0: the vendor device for frames and buttons. 1: the read-only drive and the gamepad. */
int pspmUsbStart(int as_drive) {
    int k1 = pspSdkSetK1(0), r = 0;
    drive = as_drive;
    if (drive)
        r = open_stick();
    if (r >= 0)
        r = sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, 0);
    if (r >= 0)
        r = sceUsbStart(drive ? DRIVE : DRIVER, 0, 0);
    if (r >= 0)
        r = sceUsbActivate(PID);
    started = r >= 0;
    if (started && drive) {
        SceUID t = sceKernelCreateThread("pspm_drive", drive_thread, 0x18, 0x2000, 0, NULL);
        SceUID h = sceKernelCreateThread("pspm_gamepad", hid_thread, 0x17, 0x1000, 0, NULL);
        r = t < 0 ? t : h < 0 ? h : sceKernelStartThread(t, 0, NULL);
        if (r >= 0)
            r = sceKernelStartThread(h, 0, NULL);
    }
    pspSdkSetK1(k1);
    return r;
}

/* Fill dst with size bytes (a multiple of 512) from the PC. Returns size, or < 0 when nothing could be read
   (cable out, PC not sending yet, or the drive is on). */
int pspmUsbRead(void *dst, int size) {
    if (!user_ptr(dst, size) || size & 511 || drive)
        return -1;
    int k1 = pspSdkSetK1(0), got = 0;
    while (got < size) {
        int n = transfer(&pin, size - got < pin.size ? size - got : pin.size, 0);
        if (n < 0) {
            got = -1;
            break;
        }
        memcpy((char *)dst + got, pin.buf, n);
        got += n;
    }
    pspSdkSetK1(k1);
    return got;
}

/* Send up to 512 bytes to the PC. Waits until the PC reads them; returns size, or < 0. With the drive on, the
   gamepad sends the buttons itself, so it returns at once (the caller waits for the vblank). */
int pspmUsbWrite(const void *src, int size) {
    if (!user_ptr(src, size) || size <= 0 || size > pout.size)
        return -1;
    int k1 = pspSdkSetK1(0), r = size;
    if (!drive) {
        memcpy(pout.buf, src, size);
        r = transfer(&pout, size, 1);
    }
    pspSdkSetK1(k1);
    return r;
}

/* All of the PSP's buttons, with the ones user mode can't see (the note button, HOME...). */
unsigned pspmCtrlButtons(void) {
    int k1 = pspSdkSetK1(0);
    SceCtrlData c;
    sceCtrlPeekBufferPositive(&c, 1);
    pspSdkSetK1(k1);
    return c.Buttons;
}

int module_start(SceSize args, void *argp) {
    pin.flag = sceKernelCreateEventFlag("pspm_usb_in", 0, 0, NULL);
    pout.flag = sceKernelCreateEventFlag("pspm_usb_out", 0, 0, NULL);
    din.flag = sceKernelCreateEventFlag("pspm_drive_in", 0, 0, NULL);
    dout.flag = sceKernelCreateEventFlag("pspm_drive_out", 0, 0, NULL);
    dsect.flag = sceKernelCreateEventFlag("pspm_drive_data", 0, 0, NULL);
    dhid.flag = sceKernelCreateEventFlag("pspm_gamepad", 0, 0, NULL);
    return sceUsbbdRegister(&driver) < 0 || sceUsbbdRegister(&drive_driver) < 0 ? 1 : 0;
}

int module_stop(SceSize args, void *argp) {
    if (started) {
        sceUsbDeactivate(PID);
        sceUsbStop(drive ? DRIVE : DRIVER, 0, 0);
        sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, 0);
    }
    sceUsbbdUnregister(&driver);
    sceUsbbdUnregister(&drive_driver);
    if (ms >= 0)
        sceIoClose(ms);
    return 0;
}
