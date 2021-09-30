#include "kernel/types.h"
#include "kernel/stat.h"
#include "user/user.h"

void prime(int input_fd);

int main(int argc, char const *argv[])
{
    int parent_fd[2];
    pipe(parent_fd);
    if (fork())
    {
        close(parent_fd[0]);
        int i;
        for (i = 2; i < 36; i++)
        {
            write(parent_fd[1], &i, sizeof(int));
        }
        close(parent_fd[1]);
    }
    else
    {
        close(parent_fd[1]);
        prime(parent_fd[0]);
    }
    wait(0);
    exit(0);
}

void prime(int input_fd)
{
    int base;
    /* Exit if last child */
    if (read(input_fd, &base, sizeof(int)) == 0)
    {
        exit(0);
    }
    printf("prime %d\n", base);

    /* Create new child if not last */
    int p[2];
    pipe(p);
    if (fork() == 0)
    {
        close(p[1]);
        prime(p[0]);
    }
    else
    {
        close(p[0]);
        int n;
        int eof;
        do
        {
            eof = read(input_fd, &n, sizeof(int));
            if (n % base != 0)
            {
                write(p[1], &n, sizeof(int));
            }
        } while (eof);

        close(p[1]);
    }
    wait(0);
    exit(0);
}