# PSPM Screen: EBOOT.PBP plus the kernel USB driver it loads (driver/pspm_usb.prx); both go in its game folder.
TARGET = pspm_screen
OBJS = main.o pspm_usb.o
CFLAGS = -O2 -G0 -Wall
BUILD_PRX = 1
# psputility comes from the SDK's own list after libc; naming it here splits its stubs and breaks psp-fixup-imports
LIBS = -lpspkubridge -lpsppower -lpspjpeg -lpspaudio -lpspnet_inet -lpspnet_resolver -lpspusb -lpspusbstor
EXTRA_TARGETS = EBOOT.PBP driver/pspm_usb.prx
EXTRA_CLEAN = pspm_usb.S
PSP_EBOOT_TITLE = PSPM Screen

PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak

pspm_usb.S: driver/exports.exp  # user-mode stubs for the driver's syscalls
	psp-build-exports -s $<

driver/pspm_usb.prx: driver/main.c driver/exports.exp
	$(MAKE) -C driver
