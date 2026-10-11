TARGET = pspm_info
OBJS = main.o
CFLAGS = -O2 -Wall
LIBS = -lpsppower
BUILD_PRX = 1
EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSPM Info

PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak
