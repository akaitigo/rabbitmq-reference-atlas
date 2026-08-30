package main

import (
	"bytes"
	"testing"
)

func TestRemainingLengthBoundaries(t *testing.T) {
	tests := []struct {
		value int
		want  []byte
	}{
		{0, []byte{0}},
		{127, []byte{127}},
		{128, []byte{128, 1}},
		{16384, []byte{128, 128, 1}},
	}
	for _, test := range tests {
		if got := remainingLength(test.value); !bytes.Equal(got, test.want) {
			t.Fatalf("remainingLength(%d)=%x, want %x", test.value, got, test.want)
		}
	}
}

func TestMQTTPacketFraming(t *testing.T) {
	body := bytes.Repeat([]byte{0x2a}, 128)
	packet := mqttPacket(0x30, body)
	if !bytes.Equal(packet[:3], []byte{0x30, 0x80, 0x01}) {
		t.Fatalf("unexpected MQTT frame header: %x", packet[:3])
	}
}
