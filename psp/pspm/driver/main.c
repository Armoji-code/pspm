/* PSPM's USB driver. Two devices, one at a time:
   - the vendor device (SCREEN mode): bulk endpoints the PC writes frames to and reads the buttons from;
   - the drive (CONTROLLER mode over the cable): a read-only USB drive of the memory stick, so PPSSPP plays its
     games, with TAIL extra sectors after the stick's end that hold the buttons for the PC to read.
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
#define TAIL 8    /* sectors after the stick's end; reading any of them gives the latest "PSPMPAD1" packet */

/* Same layout as PSPLink/RemoteJoyLite: ep1 bulk in, ep2 and ep3 bulk out. Frames come on ep2, buttons go on ep1. */
static struct UsbEndpoint endp[4] = {{0, 0, 0}, {1, 0, 0}, {2, 0, 0}, {3, 0, 0}};
static struct UsbInterface intf = {0xFFFFFFFF, 0, 1};
static struct UsbData data[2]; /* [0] high speed, [1] full speed */
/* The drive: ep1 bulk in, ep2 bulk out, like the PSP's own USB mode. */
static struct UsbEndpoint dendp[3] = {{0, 0, 0}, {1, 0, 0}, {2, 0, 0}};
static struct UsbInterface dintf = {0xFFFFFFFF, 0, 1};
static struct UsbData ddata[2];
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
static struct InterfaceDescriptor diface = { /* mass storage, SCSI commands, bulk-only transport */
    .bLength = 9, .bDescriptorType = 4, .bNumEndpoints = 2, .bInterfaceClass = 8, .bInterfaceSubClass = 6,
    .bInterfaceProtocol = 0x50, .iInterface = 1};

static void fill(struct UsbData *d, int packet, int drive) {
    static const unsigned char addr[3] = {0x81, 0x02, 0x03};
    int n = drive ? 2 : 3;
    struct DeviceDescriptor dv = dev;
    struct ConfigDescriptor cf = conf;
    dv.bcdDevice = drive ? 0x300 : 0x200; /* 0x300: the drive, so PSPM on the PC tells them apart */
    cf.wTotalLength = 9 + 9 + n * 7;
    memcpy(d->devdesc, &dv, sizeof dv);
    d->config.pconfdesc = &d->confdesc;
    d->config.pinterfaces = &d->interfaces;
    d->config.pinterdesc = &d->interdesc;
    d->config.pendp = &d->endp[0];
    memcpy(d->confdesc.desc, &cf, sizeof cf);
    d->confdesc.pinterfaces = &d->interfaces;
    d->interfaces.pinterdesc[0] = &d->interdesc;
    d->interfaces.intcount = 1;
    memcpy(d->interdesc.desc, drive ? &diface : &iface, sizeof iface);
    d->interdesc.pendp = &d->endp[0];
    for (int i = 0; i < n; i++) {
        struct EndpointDescriptor e = {7, 5, addr[i], 2, packet, 0};
        memcpy(d->endp[i].desc, &e, sizeof e);
    }
}

static int none(void) { return 0; }

static int start_func(int size, void *args) {
    memset(data, 0, sizeof data);
    fill(&data[0], 512, 0);
    fill(&data[1], 64, 0);
    return 0;
}

static int drive_start(int size, void *args) {
    memset(ddata, 0, sizeof ddata);
    fill(&ddata[0], 512, 1);
    fill(&ddata[1], 64, 1);
    return 0;
}

/* Class requests on the drive: GET MAX LUN (one drive, LUN 0); anything else is refused. */
static struct UsbdDeviceReq ctl;
static unsigned char lun[64] __attribute__((aligned(64)));

static int drive_ctl(int a, int b, struct DeviceRequest *r) {
    if (r->bRequest != 0xFE)
        return -1;
    memset(&ctl, 0, sizeof ctl);
    lun[0] = 0;
    sceKernelDcacheWritebackRange(lun, sizeof lun);
    ctl.endp = &dendp[0];
    ctl.data = lun;
    ctl.size = 1;
    ctl.func = none;
    return sceUsbbdReqSend(&ctl) < 0 ? -1 : 0;
}

static struct UsbDriver driver = {
    DRIVER, 4, endp, &intf, &data[0].devdesc[0], &data[0].config, &data[1].devdesc[0], &data[1].config, &str,
    (void *)none, (void *)none, (void *)none, (void *)none, 0, start_func, (void *)none, NULL};
static struct UsbDriver drive_driver = {
    DRIVE, 3, dendp, &dintf, &ddata[0].devdesc[0], &ddata[0].config, &ddata[1].devdesc[0], &ddata[1].config, &str,
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
static unsigned char pad_now[512] __attribute__((aligned(64)));
static unsigned char sense[3]; /* key, ASC, ASCQ of the last failure, for REQUEST SENSE */

static unsigned le32(const unsigned char *p) { return p[0] | p[1] << 8 | p[2] << 16 | (unsigned)p[3] << 24; }
static unsigned be32(const unsigned char *p) { return (unsigned)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
static void put_be32(unsigned char *p, unsigned v) {
    p[0] = v >> 24, p[1] = v >> 16, p[2] = v >> 8, p[3] = v;
}

static int send_sectors(unsigned lba, unsigned count) {
    while (count) {
        unsigned n = count < sizeof sect / 512 ? count : sizeof sect / 512;
        unsigned real = lba >= ms_sectors ? 0 : ms_sectors - lba < n ? ms_sectors - lba : n;
        if (real && (sceIoLseek(ms, (SceOff)lba * 512, PSP_SEEK_SET) < 0 || sceIoRead(ms, sect, real * 512) != real * 512))
            return -1;
        for (unsigned i = real; i < n; i++)
            memcpy(sect + i * 512, pad_now, 512);
        if (transfer(&dsect, n * 512, 1) < 0)
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
        put_be32(r, ms_sectors + TAIL - 1);
        put_be32(r + 4, 512);
        n = 8;
        break;
    case 0x28: { /* READ (10) */
        unsigned lba = be32(cb + 2), count = cb[7] << 8 | cb[8];
        if (lba + count > ms_sectors + TAIL || count * 512 > want || send_sectors(lba, count) < 0)
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

/* as_drive 0: the vendor device for frames and buttons. 1: the read-only drive with the buttons after it. */
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
        r = t < 0 ? t : sceKernelStartThread(t, 0, NULL);
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

/* Send up to 512 bytes to the PC. Waits until the PC reads them; returns size, or < 0. With the drive on, it
   only becomes what the PC reads after the stick, so it returns at once (the caller waits for the vblank). */
int pspmUsbWrite(const void *src, int size) {
    if (!user_ptr(src, size) || size <= 0 || size > pout.size)
        return -1;
    int k1 = pspSdkSetK1(0), r = size;
    if (drive)
        memcpy(pad_now, src, size);
    else {
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
