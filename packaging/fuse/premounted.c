/*
 * Preloaded into ntfs-3g by the kiosk (pandora_box.fuse): the FUSE
 * connection is mounted by the kiosk, ntfs-3g only serves it.
 *
 * ntfs-3g carries its own FUSE library, which opens /dev/fuse and mounts it
 * itself, a privilege its sandbox does not have; unlike libfuse 3, it cannot
 * take a mounted connection (mount point /dev/fd/N). The kiosk opens
 * /dev/fuse, mounts it, then starts ntfs-3g with the descriptor and
 * PANDORA_BOX_FUSE_FD=N: here, opening /dev/fuse returns a copy of that
 * descriptor, and mounting or unmounting a FUSE filesystem does nothing.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <unistd.h>

static int fuse_fd(const char *path)
{
	const char *value = getenv("PANDORA_BOX_FUSE_FD");
	char *end;
	long fd;

	if (path == NULL || strcmp(path, "/dev/fuse") != 0 || value == NULL)
		return -1;
	fd = strtol(value, &end, 10);
	if (*value == '\0' || *end != '\0' || fd < 0 || fd > 1024)
		return -1;
	return fcntl((int)fd, F_DUPFD_CLOEXEC, 0);
}

static int next_open(const char *name, const char *path, int flags, mode_t mode)
{
	int (*real)(const char *, int, ...) = dlsym(RTLD_NEXT, name);

	if (real == NULL) {
		errno = ENOSYS;
		return -1;
	}
	return real(path, flags, mode);
}

#define OPEN(name)                                                     \
	int name(const char *path, int flags, ...)                     \
	{                                                              \
		mode_t mode = 0;                                       \
		int fd = fuse_fd(path);                                \
		if (fd >= 0)                                           \
			return fd;                                     \
		if (flags & (O_CREAT | O_TMPFILE)) {                   \
			va_list ap;                                    \
			va_start(ap, flags);                           \
			mode = va_arg(ap, mode_t);                     \
			va_end(ap);                                    \
		}                                                      \
		return next_open(#name, path, flags, mode);            \
	}

OPEN(open)
OPEN(open64)

/* Fortified callers (_FORTIFY_SOURCE): flags without O_CREAT */
int __open_2(const char *path, int flags)
{
	int fd = fuse_fd(path);

	return fd >= 0 ? fd : next_open("__open_2", path, flags, 0);
}

int __open64_2(const char *path, int flags)
{
	int fd = fuse_fd(path);

	return fd >= 0 ? fd : next_open("__open64_2", path, flags, 0);
}

int mount(const char *source, const char *target, const char *type,
	  unsigned long flags, const void *data)
{
	int (*real)(const char *, const char *, const char *, unsigned long,
		    const void *) = dlsym(RTLD_NEXT, "mount");

	if (type != NULL && strncmp(type, "fuse", 4) == 0)
		return 0;
	if (real == NULL) {
		errno = ENOSYS;
		return -1;
	}
	return real(source, target, type, flags, data);
}

/* The kiosk unmounts the connection */
int umount2(const char *target, int flags)
{
	(void)target;
	(void)flags;
	return 0;
}

int umount(const char *target)
{
	(void)target;
	return 0;
}
