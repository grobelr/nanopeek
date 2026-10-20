# Example for the Dockerfile viewer.
FROM alpine:3.22
WORKDIR /app
RUN printf 'Hello from nanopeek!\n' > greeting.txt
CMD ["cat", "greeting.txt"]
