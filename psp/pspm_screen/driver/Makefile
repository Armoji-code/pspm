TARGET = pspm_usb
OBJS = main.o
CFLAGS = -O2 -G0 -Wall
BUILD_PRX = 1
PRX_EXPORTS = exports.exp
USE_KERNEL_LIBC = 1
USE_KERNEL_LIBS = 1
LIBS = -lpspusb_driver -lpspusbbus_driver

PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
