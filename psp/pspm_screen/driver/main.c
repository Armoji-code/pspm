/* PSPM Screen's USB driver: a vendor device with bulk endpoints the PC writes frames to. Kernel mode, since only
   kernel code can register a USB driver; the app calls pspmUsbStart/Read/Write through syscalls. */
#include <pspkernel.h>
#include <pspsdk.h>
#include <pspusb.h>
#include <pspusbbus.h>
#include <pspctrl.h>
#include <string.h>

PSP_MODULE_INFO("pspm_usb", PSP_MODULE_KERNEL, 1, 0);

#define DRIVER "PSPMScreenDriver"
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

static struct UsbDriver driver = {
    DRIVER, 4, endp, &intf, &data[0].devdesc[0], &data[0].config, &data[1].devdesc[0], &data[1].config, &str,
    (void *)none, (void *)none, (void *)none, (void *)none, 0, start_func, (void *)none, NULL};

/* One request, event flag and buffer per direction: frames come in on ep2 while buttons go out on ep1. */
struct pipe {
    struct UsbdDeviceReq req;
    SceUID flag;
    int endp;
    char *buf;
    int size;
};
static char in_buf[64 * 1024] __attribute__((aligned(64)));
static char out_buf[512] __attribute__((aligned(64)));
static struct pipe pin = {.endp = 2, .buf = in_buf, .size = sizeof in_buf};
static struct pipe pout = {.endp = 1, .buf = out_buf, .size = sizeof out_buf};
static int started;

static int done(struct UsbdDeviceReq *r, int a, int b) {
    sceKernelSetEventFlag(((struct pipe *)r->arg)->flag, 1);
    return 0;
}

/* Queue one transfer of n bytes on p's endpoint and wait for it. Returns bytes moved, or < 0. */
static int transfer(struct pipe *p, int n, int send) {
    memset(&p->req, 0, sizeof p->req);
    p->req.endp = &endp[p->endp];
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

static int user_ptr(const void *p, int size) { /* a user pointer only, never kernel memory */
    return !(((unsigned)p | ((unsigned)p + size)) >> 31);
}

int pspmUsbStart(void) {
    int k1 = pspSdkSetK1(0);
    int r = sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, 0);
    if (r >= 0)
        r = sceUsbStart(DRIVER, 0, 0);
    if (r >= 0)
        r = sceUsbActivate(PID);
    started = r >= 0;
    pspSdkSetK1(k1);
    return r;
}

/* Fill dst with size bytes (a multiple of 512) from the PC. Returns size, or < 0 when nothing could be read
   (cable out, PC not sending yet). */
int pspmUsbRead(void *dst, int size) {
    if (!user_ptr(dst, size) || size & 511)
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

/* Send up to 512 bytes to the PC. Waits until the PC reads them; returns size, or < 0. */
int pspmUsbWrite(const void *src, int size) {
    if (!user_ptr(src, size) || size <= 0 || size > pout.size)
        return -1;
    int k1 = pspSdkSetK1(0);
    memcpy(pout.buf, src, size);
    int r = transfer(&pout, size, 1);
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
    return sceUsbbdRegister(&driver) < 0 ? 1 : 0;
}

int module_stop(SceSize args, void *argp) {
    if (started) {
        sceUsbDeactivate(PID);
        sceUsbStop(DRIVER, 0, 0);
        sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, 0);
    }
    sceUsbbdUnregister(&driver);
    sceKernelDeleteEventFlag(pin.flag);
    sceKernelDeleteEventFlag(pout.flag);
    return 0;
}
