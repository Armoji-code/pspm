/* PSPM Screen's USB driver: a vendor device with bulk endpoints the PC writes frames to. Kernel mode, since only
   kernel code can register a USB driver; the app calls pspmUsbStart/pspmUsbRead through syscalls. */
#include <pspkernel.h>
#include <pspsdk.h>
#include <pspusb.h>
#include <pspusbbus.h>
#include <string.h>

PSP_MODULE_INFO("pspm_usb", PSP_MODULE_KERNEL, 1, 0);

#define DRIVER "PSPMScreenDriver"
#define PID 0x1c9 /* PSPLink's and RemoteJoyLite's "Type B": their udev rule and Windows driver already cover it */

/* Same layout as PSPLink/RemoteJoyLite: ep1 bulk in, ep2 and ep3 bulk out. PSPM writes frames to ep2. */
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

static SceUID flag = -1;
static struct UsbdDeviceReq req;
static char buf[64 * 1024] __attribute__((aligned(64)));
static int started;

static int recv_done(struct UsbdDeviceReq *r, int a, int b) {
    sceKernelSetEventFlag(flag, 1);
    return 0;
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
    if (((unsigned)dst | ((unsigned)dst + size)) >> 31 || size & 511)
        return -1; /* a user pointer only, never kernel memory */
    int k1 = pspSdkSetK1(0), got = 0;
    while (got < size) {
        int n = size - got < (int)sizeof buf ? size - got : (int)sizeof buf;
        sceKernelDcacheInvalidateRange(buf, n);
        memset(&req, 0, sizeof req);
        req.endp = &endp[2];
        req.data = buf;
        req.size = n;
        req.func = recv_done;
        sceKernelClearEventFlag(flag, 0);
        if (sceUsbbdReqRecv(&req) < 0 || sceKernelWaitEventFlag(flag, 1, PSP_EVENT_WAITOR | PSP_EVENT_WAITCLEAR, NULL,
                                                                 NULL) < 0 || req.retcode || req.recvsize <= 0) {
            got = -1;
            break;
        }
        memcpy((char *)dst + got, buf, req.recvsize);
        got += req.recvsize;
    }
    pspSdkSetK1(k1);
    return got;
}

int module_start(SceSize args, void *argp) {
    flag = sceKernelCreateEventFlag("pspm_usb", 0, 0, NULL);
    return sceUsbbdRegister(&driver) < 0 ? 1 : 0;
}

int module_stop(SceSize args, void *argp) {
    if (started) {
        sceUsbDeactivate(PID);
        sceUsbStop(DRIVER, 0, 0);
        sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, 0);
    }
    sceUsbbdUnregister(&driver);
    sceKernelDeleteEventFlag(flag);
    return 0;
}
