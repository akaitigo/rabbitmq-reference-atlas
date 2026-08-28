package main

import (
	"bytes"
	"io"
	"net"
	"testing"
)

func TestExchangeHeader(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	done := make(chan error, 1)
	go func() {
		connection, acceptErr := listener.Accept()
		if acceptErr != nil {
			done <- acceptErr
			return
		}
		defer connection.Close()
		header := make([]byte, 8)
		if _, readErr := io.ReadFull(connection, header); readErr != nil {
			done <- readErr
			return
		}
		if !bytes.Equal(header, saslHeader) {
			done <- io.ErrUnexpectedEOF
			return
		}
		_, writeErr := connection.Write(saslHeader)
		done <- writeErr
	}()
	received, err := exchangeHeader(listener.Addr().String(), saslHeader)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(received, saslHeader) {
		t.Fatalf("unexpected response: %x", received)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}

func TestAllPassed(t *testing.T) {
	if allPassed(nil) {
		t.Fatal("empty checks must not pass")
	}
	if allPassed([]observation{{Passed: true}, {Passed: false}}) {
		t.Fatal("a failed check must fail the scenario")
	}
	if !allPassed([]observation{{Passed: true}, {Passed: true}}) {
		t.Fatal("all passing checks should pass")
	}
}
