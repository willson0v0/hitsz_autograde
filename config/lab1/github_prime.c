#include "kernel/types.h"
#include "kernel/stat.h"
#include "user/user.h"

#define MAX 36
#define FIRST_PRIME 2

int generate_natural();  // -> out_fd
int prime_filter(int in_fd, int prime);  // -> out_fd

int
main(int argc, char *argv[])
{
	int prime; 
	
	int in = generate_natural();
	while (read(in, &prime, sizeof(int))) {
		// printf("prime %d: in_fd: %d\n", prime, in);  // debug
		printf("prime %d\n", prime); 
		in = prime_filter(in, prime);
	}

	exit(0);
}

// 生成自然数: 2, 3, 4, ..< MAX
int
generate_natural() {
	int out_pipe[2];
	
	pipe(out_pipe);

	if (!fork()) {
		for (int i = FIRST_PRIME; i < MAX; i++) {
			write(out_pipe[1], &i, sizeof(int));
		}
		close(out_pipe[1]);

		exit(0);
	}

	close(out_pipe[1]);

	return out_pipe[0];
}

// 素数筛
int 
prime_filter(int in_fd, int prime) 
{
	int num;
	int out_pipe[2];

	pipe(out_pipe);

	if (!fork()) {
		while (read(in_fd, &num, sizeof(int))) {
			if (num % prime) {
				write(out_pipe[1], &num, sizeof(int));
			}
		}
		close(in_fd);
		close(out_pipe[1]);
		
		exit(0);
	}

	close(in_fd);
	close(out_pipe[1]);

	return out_pipe[0];
}