# SPDX-License-Identifier: ISC
#
# Copyright (C) 2026 Donnie V. Savage
#
# Behavioral coverage for Cisco IOS XE multiprotocol/v2 IPv6 wide route TLVs.

from pathlib import Path
import os
import subprocess
import sys
import textwrap


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "eigrp" / "code").is_dir())


def test_ipv6_wide_tlv2_codec_round_trip_and_rejection(tmp_path):
    source = tmp_path / "tlv2_ipv6.c"
    binary = tmp_path / "tlv2_ipv6"
    source.write_text(
        textwrap.dedent(
            r'''
            #include <arpa/inet.h>
            #include <assert.h>
            #include <stdbool.h>
            #include <stdarg.h>
            #include <stdio.h>
            #include <stdint.h>
            #include <string.h>

            #include "eigrp/code/eigrp.h"
            #include "eigrp/code/eigrp_log.h"
            #include "eigrp/code/eigrp_filter.h"
            #include "eigrp/code/eigrp_fsm.h"
            #include "eigrp/code/eigrp_metric.h"
            #include "eigrp/code/eigrp_prefix.h"
            #include "eigrp/code/eigrp_stream.h"
            #include "eigrp/code/eigrp_structs.h"
            #include "eigrp/code/eigrp_packet.h"
            #include "eigrp/code/eigrp_neighbor.h"
            #include "eigrp/code/eigrp_tlv1.h"
            #include "eigrp/code/eigrp_tlv2.h"

            static eigrp_route_descriptor_t decoded_route;
            static unsigned int route_free_count;

            eigrp_route_descriptor_t *eigrp_topology_route_create(eigrp_intf_t *ei)
            {
                memset(&decoded_route, 0, sizeof(decoded_route));
                decoded_route.ei = ei;
                return &decoded_route;
            }

            void eigrp_topology_route_free(eigrp_route_descriptor_t *route)
            {
                assert(route == &decoded_route);
                route_free_count++;
            }

            bool eigrp_filter_prefix_update(eigrp_instance_t *eigrp,
                                           eigrp_intf_t *ei, int direction,
                                           const eigrp_prefix_t *prefix)
            {
                (void)eigrp;
                (void)ei;
                (void)direction;
                (void)prefix;
                return false;
            }

            bool eigrp_debug_packet_any_enabled(uint8_t direction)
            {
                (void)direction;
                return false;
            }

            void eigrp_log(eigrp_log_level_t level, const char *format, ...)
            {
                (void)level;
                (void)format;
            }

            const char *eigrp_print_addr(const eigrp_addr_t *address)
            {
                (void)address;
                return "test";
            }

            eigrp_scaled_t eigrp_delay_to_scaled(eigrp_delay_t delay)
            {
                delay = delay ? delay : EIGRP_DELAY_MIN;
                return delay * EIGRP_CLASSIC_SCALER;
            }

            eigrp_delay_t eigrp_scaled_to_delay(eigrp_scaled_t scaled)
            {
                eigrp_delay_t delay = scaled / EIGRP_CLASSIC_SCALER;
                return delay ? delay : EIGRP_DELAY_MIN;
            }

            int eigrp_sys_packet_send(eigrp_instance_t *eigrp,
                                      eigrp_intf_t *ei,
                                      const eigrp_address_t *destination,
                                      const uint8_t *payload, size_t length)
            {
                (void)eigrp; (void)ei; (void)destination; (void)payload; (void)length;
                return -1;
            }

            bool eigrp_sys_packet_receive(eigrp_instance_t *eigrp,
                                          uint8_t *buffer, size_t capacity,
                                          size_t *received_length,
                                          eigrp_ifindex_t *ifindex,
                                          eigrp_address_t *source,
                                          eigrp_address_t *destination,
                                          eigrp_packet_rx_meta_t *meta)
            {
                (void)eigrp; (void)buffer; (void)capacity; (void)received_length;
                (void)ifindex; (void)source; (void)destination; (void)meta;
                return false;
            }

            eigrp_intf_t *eigrp_intf_lookup_by_ifindex(eigrp_instance_t *eigrp,
                                                            eigrp_ifindex_t ifindex)
            {
                (void)eigrp;
                (void)ifindex;
                return NULL;
            }

            eigrp_intf_t *eigrp_intf_lookup_by_local_addr(eigrp_instance_t *eigrp,
                                                         const eigrp_addr_t *source)
            {
                (void)eigrp; (void)source;
                return NULL;
            }

            static unsigned int ipv6_prefix_octets(uint8_t prefix_length)
            {
                if (prefix_length == 0)
                    return 0;
                if (prefix_length == 128)
                    return 16;
                return (prefix_length / 8U) + 1U;
            }

            static void metric_set(eigrp_metrics_t *metric)
            {
                memset(metric, 0, sizeof(*metric));
                metric->delay = 10;
                metric->bandwidth = 0x000a0000;
                metric->mtu[0] = 0xdc;
                metric->mtu[1] = 0x05;
                metric->mtu[2] = 0x00;
                metric->hop_count = 3;
                metric->reliability = 255;
                metric->load = 7;
                metric->tag = 9;
                metric->flags = 1;
            }

            static void prefix_set(eigrp_prefix_t *prefix, uint8_t prefix_length)
            {
                memset(prefix, 0, sizeof(*prefix));
                prefix->address.afi = EIGRP_AFI_IPV6;
                prefix->prefix_length = prefix_length;
                assert(inet_pton(AF_INET6, "2001:db8:1234:5678:9abc:def0:1234:5678",
                                 prefix->address.bytes) == 1);
                eigrp_prefix_normalize(prefix);
            }

            static void route_set(eigrp_route_descriptor_t *route, bool external,
                                  uint8_t prefix_length)
            {
                memset(route, 0, sizeof(*route));
                route->type = external ? EIGRP_EXT : EIGRP_INT;
                route->nexthop.afi = AF_INET6;
                assert(inet_pton(AF_INET6, "fe80::1234", &route->nexthop.ip.v6) == 1);
                prefix_set(&route->dest, prefix_length);
                metric_set(&route->metric);
                if (external) {
                    route->extdata.orig = 0x0a000001;
                    route->extdata.as = 4453;
                    route->extdata.tag = 0x12345678;
                    route->extdata.metric = 20;
                    route->extdata.reserved = 0;
                    route->extdata.protocol = 6;
                    route->extdata.flags = 1;
                }
            }

            static void assert_metric(const eigrp_metrics_t *metric)
            {
                assert(metric->delay == 10);
                assert(metric->bandwidth == 0x000a0000);
                assert(metric->mtu[0] == 0xdc);
                assert(metric->mtu[1] == 0x05);
                assert(metric->mtu[2] == 0x00);
                assert(metric->hop_count == 3);
                assert(metric->reliability == 255);
                assert(metric->load == 7);
                assert(metric->tag == 9);
                assert(metric->flags == 1);
            }

            static void round_trip(eigrp_instance_t *eigrp, eigrp_intf_t *ei,
                                   eigrp_nbr_t *nbr, eigrp_tlv_codec_t *codec,
                                   bool external, uint8_t prefix_length)
            {
                eigrp_route_descriptor_t route;
                eigrp_route_descriptor_t *decoded;
                eigrp_stream_t *stream = eigrp_stream_create(128);
                unsigned int prefix_octets = ipv6_prefix_octets(prefix_length);
                uint16_t expected_type = external ? EIGRP_TLV_MP_EXT : EIGRP_TLV_MP_INT;
                uint16_t expected_length = 4 + 8 + 24 + 16 + (external ? 20 : 0) + 1 + prefix_octets;

                assert(stream != NULL);
                route_set(&route, external, prefix_length);
                assert(codec->encoder(eigrp, ei, nbr, stream, &route) == expected_length);
                assert(stream->endp == expected_length);
                assert(stream->data[0] == (expected_type >> 8));
                assert(stream->data[1] == (expected_type & 0xff));
                assert(stream->data[2] == (expected_length >> 8));
                assert(stream->data[3] == (expected_length & 0xff));
                assert(stream->data[4] == 0x00 && stream->data[5] == EIGRP_TOPOLOGY_ID_BASE);
                assert(stream->data[6] == 0x00 && stream->data[7] == EIGRP_AF_IPv6);
                assert(stream->data[12] == 0);
                if (external) {
                    assert(stream->data[60] == 0x12 && stream->data[61] == 0x34);
                    assert(stream->data[62] == 0x56 && stream->data[63] == 0x78);
                }
                assert(memcmp(stream->data + 36,
                              &route.nexthop.ip.v6, 16) == 0);
                assert(stream->data[expected_length - (1 + prefix_octets)] == prefix_length);

                printf("VECTOR|%s|%u|", external ? "external" : "internal", prefix_length);
                for (size_t n = 0; n < stream->endp; n++)
                    printf("%02x", stream->data[n]);
                printf("\n");

                decoded = codec->decoder(eigrp, nbr, stream, (uint16_t)stream->endp);
                assert(decoded != NULL);
                assert(decoded->type == (external ? EIGRP_TLV_IPv6_EXT : EIGRP_TLV_IPv6_INT));
                assert(decoded->topology_id == EIGRP_TOPOLOGY_ID_BASE);
                assert(decoded->nexthop.afi == AF_INET6);
                assert(memcmp(&decoded->nexthop.ip.v6, &route.nexthop.ip.v6, 16) == 0);
                assert(decoded->dest.address.afi == EIGRP_AFI_IPV6);
                assert(decoded->dest.prefix_length == prefix_length);
                assert(memcmp(decoded->dest.address.bytes, route.dest.address.bytes, 16) == 0);
                assert_metric(&decoded->metric);
                if (external) {
                    assert(decoded->extdata.orig == route.extdata.orig);
                    assert(decoded->extdata.tag == route.extdata.tag);
                    assert(decoded->extdata.as == route.extdata.as);
                    assert(decoded->extdata.metric == route.extdata.metric);
                    assert(decoded->extdata.protocol == route.extdata.protocol);
                    assert(decoded->extdata.flags == route.extdata.flags);
                }
                assert(stream->getp == stream->endp);
                eigrp_stream_free(stream);
            }

            static void reject_bytes(eigrp_instance_t *eigrp, eigrp_nbr_t *nbr,
                                     eigrp_tlv_codec_t *codec,
                                     const uint8_t *bytes, size_t length)
            {
                eigrp_stream_t *stream = eigrp_stream_create(length ? length : 1);
                assert(stream != NULL);
                assert(eigrp_stream_put(stream, bytes, length) == length);
                assert(codec->decoder(eigrp, nbr, stream, (uint16_t)length) == NULL);
                assert(stream->getp == stream->endp);
                eigrp_stream_free(stream);
            }

            int main(void)
            {
                static const uint8_t prefix_lengths[] = {0, 1, 7, 8, 9, 63, 64, 65, 127, 128};
                eigrp_instance_t eigrp = {0};
                eigrp_intf_t interface = {0};
                eigrp_nbr_t neighbor = {0};
                eigrp_tlv_codec_t codec = {0};
                eigrp_tlv_codec_t tlv1 = {0};
                eigrp_route_descriptor_t route;
                eigrp_route_descriptor_t *decoded;
                eigrp_stream_t *valid;
                eigrp_stream_t *mixed;
                uint8_t short_header[] = {0x04, 0x02, 0x00};
                uint8_t too_long[] = {0x04, 0x02, 0x00, 0x40};
                size_t prefix_offset;

                eigrp_ipv6_init(&eigrp.af_vectors);
                eigrp_tlv1_init(&tlv1);
                eigrp_tlv2_init(&codec);
                interface.eigrp = &eigrp;
                neighbor.ei = &interface;

                /* Forced `metric version 32bit` packet policy selects TLV1. */
                eigrp.metric_version = EIGRP_TLV_64B_VERSION;
                eigrp.tlv1_codec = tlv1;
                eigrp.tlv2_codec = codec;
                neighbor.tlv_version = EIGRP_TLV_64B_VERSION;
                neighbor.encoder = eigrp.tlv2_codec.encoder;
                neighbor.decoder = eigrp.tlv2_codec.decoder;
                assert(neighbor.encoder == codec.encoder);
                assert(neighbor.decoder == codec.decoder);

                for (size_t i = 0; i < sizeof(prefix_lengths); i++) {
                    round_trip(&eigrp, &interface, &neighbor, &codec, false,
                               prefix_lengths[i]);
                    round_trip(&eigrp, &interface, &neighbor, &codec, true,
                               prefix_lengths[i]);
                }

                reject_bytes(&eigrp, &neighbor, &codec, short_header, sizeof(short_header));
                reject_bytes(&eigrp, &neighbor, &codec, too_long, sizeof(too_long));



                /* Actual Cisco CSR1000v 17.03.06 multiprotocol UPDATE TLVs,
                 * captured during OE/Cisco 2R reinitialization on 2026-10-27.
                 * Decode the production bytes, not a synthetic OE round trip.
                 */
                static const uint8_t cisco_v4[] = { 0x06, 0x02, 0x00, 0x2d, 0x00, 0x00, 0x00, 0x01, 0x0a, 0xff, 0x00, 0x02, 0x00, 0x00, 0xff, 0x01, 0x00, 0x05, 0xea, 0x00, 0x00, 0x00, 0x00, 0x13, 0x12, 0xd0, 0x00, 0x00, 0x00, 0x7a, 0x12, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x20, 0x0a, 0xff, 0x00, 0x02 };
                static const uint8_t cisco_v6[] = { 0x06, 0x02, 0x00, 0x45, 0x00, 0x00, 0x00, 0x02, 0x0a, 0xff, 0x00, 0x02, 0x00, 0x00, 0xff, 0x01, 0x00, 0x05, 0xea, 0x00, 0x00, 0x00, 0x00, 0x13, 0x12, 0xd0, 0x00, 0x00, 0x00, 0x7a, 0x12, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80, 0x20, 0x01, 0x0d, 0xb8, 0x44, 0x73, 0x00, 0xff, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02 };
                eigrp_stream_t *capture;
                eigrp_ipv4_init(&eigrp.af_vectors);
                memset(&neighbor.src, 0, sizeof(neighbor.src));
                neighbor.src.afi = AF_INET;
                assert(inet_pton(AF_INET, "10.44.53.2", &neighbor.src.ip.v4) == 1);
                capture = eigrp_stream_create(sizeof(cisco_v4));
                assert(capture != NULL);
                assert(eigrp_stream_put(capture, cisco_v4, sizeof(cisco_v4)) == sizeof(cisco_v4));
                decoded = codec.decoder(&eigrp, &neighbor, capture, sizeof(cisco_v4));
                assert(decoded != NULL);
                assert(decoded->dest.prefix_length == 32);
                assert(decoded->dest.address.afi == EIGRP_AFI_IPV4);
                assert(memcmp(decoded->dest.address.bytes, "\x0a\xff\x00\x02", 4) == 0);
                assert(decoded->nexthop.afi == AF_INET);
                assert(memcmp(&decoded->nexthop.ip.v4, &neighbor.src.ip.v4, 4) == 0);
                assert(capture->getp == capture->endp);
                eigrp_stream_free(capture);

                eigrp_ipv6_init(&eigrp.af_vectors);
                memset(&neighbor.src, 0, sizeof(neighbor.src));
                neighbor.src.afi = AF_INET6;
                assert(inet_pton(AF_INET6, "fe80::2", &neighbor.src.ip.v6) == 1);
                capture = eigrp_stream_create(sizeof(cisco_v6));
                assert(capture != NULL);
                assert(eigrp_stream_put(capture, cisco_v6, sizeof(cisco_v6)) == sizeof(cisco_v6));
                decoded = codec.decoder(&eigrp, &neighbor, capture, sizeof(cisco_v6));
                assert(decoded != NULL);
                assert(decoded->dest.prefix_length == 128);
                assert(decoded->dest.address.afi == EIGRP_AFI_IPV6);
                assert(decoded->topology_id == 0);
                assert(decoded->nexthop.afi == AF_INET6);
                assert(memcmp(&decoded->nexthop.ip.v6, &neighbor.src.ip.v6, 16) == 0);
                assert(capture->getp == capture->endp);
                eigrp_stream_free(capture);

                /* Unsupported AFIs and missing next hops must not enter DUAL. */
                capture = eigrp_stream_create(sizeof(cisco_v6));
                assert(capture != NULL);
                assert(eigrp_stream_put(capture, cisco_v6, sizeof(cisco_v6)) == sizeof(cisco_v6));
                capture->data[7] = 3;
                assert(codec.decoder(&eigrp, &neighbor, capture, sizeof(cisco_v6)) == NULL);
                eigrp_stream_free(capture);
                /* A mixed-capability interface emits one classic and one MP TLV. */
                mixed = eigrp_stream_create(256);
                assert(mixed != NULL);
                route_set(&route, false, 64);
                assert(eigrp_packet_encoder_both(&eigrp, &interface, NULL, mixed, &route)
                       == 46 + 62);
                assert(mixed->data[0] == 0x04 && mixed->data[1] == 0x02);
                assert(mixed->data[46] == 0x06 && mixed->data[47] == 0x02);
                eigrp_stream_free(mixed);

                /* Truncate the mandatory IPv6 next-hop field. */
                valid = eigrp_stream_create(128);
                assert(valid != NULL);
                route_set(&route, false, 64);
                assert(codec.encoder(&eigrp, &interface, &neighbor, valid, &route) == 62);
                valid->endp = 51; /* TLV header, metric and only 15 next-hop bytes */
                valid->data[3] = 51;
                assert(codec.decoder(&eigrp, &neighbor, valid, (uint16_t)valid->endp) == NULL);
                assert(valid->getp == valid->endp);
                eigrp_stream_free(valid);

                /* Multiprotocol TIDs are portable protocol identities, not CLI capability gates. */
                valid = eigrp_stream_create(128);
                assert(valid != NULL);
                route_set(&route, false, 64);
                route.topology_id = 1;
                assert(codec.encoder(&eigrp, &interface, &neighbor, valid, &route) == 62);
                assert(valid->data[4] == 0x00 && valid->data[5] == 0x01);
                valid->getp = 0;
                decoded = codec.decoder(&eigrp, &neighbor, valid, (uint16_t)valid->endp);
                assert(decoded != NULL);
                assert(decoded->topology_id == 1);
                assert(valid->getp == valid->endp);
                eigrp_stream_free(valid);

                /* IPv4 AFI in an IPv6 runtime is skipped as a non-matching MP TLV. */
                valid = eigrp_stream_create(128);
                assert(valid != NULL);
                route_set(&route, false, 64);
                assert(codec.encoder(&eigrp, &interface, &neighbor, valid, &route) == 62);
                valid->data[7] = EIGRP_AF_IPv4;
                assert(codec.decoder(&eigrp, &neighbor, valid, (uint16_t)valid->endp) == NULL);
                assert(valid->getp == valid->endp);
                eigrp_stream_free(valid);

                /* Start from a valid /128 internal TLV, then truncate its destination. */
                valid = eigrp_stream_create(128);
                assert(valid != NULL);
                route_set(&route, false, 128);
                assert(codec.encoder(&eigrp, &interface, &neighbor, valid, &route) == 69);
                valid->endp--;
                valid->data[2] = (uint8_t)(valid->endp >> 8);
                valid->data[3] = (uint8_t)(valid->endp & 0xff);
                assert(codec.decoder(&eigrp, &neighbor, valid, (uint16_t)valid->endp) == NULL);
                assert(valid->getp == valid->endp);
                assert(route_free_count > 0);
                eigrp_stream_free(valid);

                /* Invalid IPv6 prefix lengths are rejected without partial decode. */
                valid = eigrp_stream_create(128);
                assert(valid != NULL);
                route_set(&route, false, 64);
                assert(codec.encoder(&eigrp, &interface, &neighbor, valid, &route) == 62);
                prefix_offset = 4 + 8 + 24 + 16;
                valid->data[prefix_offset] = 129;
                assert(codec.decoder(&eigrp, &neighbor, valid, (uint16_t)valid->endp) == NULL);
                assert(valid->getp == valid->endp);
                eigrp_stream_free(valid);

                /* Real 2026-10-28 CSR1000v 17.03.06 and OE captures.
                 * Include Cisco-tagged 20-byte and RFC 16-byte exteriors
                 * in both address families, not just round trips.
                 */
                static const uint8_t external_csr4[] = {0x06, 0x03, 0x00, 0x47, 0x00, 0x00, 0x00, 0x01, 0x0a, 0xff, 0x00, 0x02, 0x03, 0x00, 0xff, 0x01, 0x00, 0x05, 0xdc, 0x00, 0x00, 0x00, 0x05, 0xf5, 0xe1, 0x00, 0x00, 0x00, 0x00, 0x01, 0x86, 0xa0, 0x00, 0x00, 0x00, 0x00, 0x02, 0x02, 0x00, 0x00, 0x00, 0x07, 0x00, 0x00, 0x00, 0x00, 0x0a, 0xff, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0x20, 0xc6, 0x13, 0xff, 0x02};
                static const uint8_t external_legacy4[] = {0x06, 0x03, 0x00, 0x3d, 0x00, 0x00, 0x00, 0x01, 0x0a, 0xff, 0x00, 0x01, 0x00, 0x00, 0xff, 0x01, 0x00, 0x05, 0xdc, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0a, 0x00, 0x00, 0x00, 0x00, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0a, 0xff, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0x20, 0xc6, 0x12, 0xff, 0x01};
                static const uint8_t external_csr6[] = {0x06, 0x03, 0x00, 0x59, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff, 0x01, 0x00, 0x05, 0xdc, 0x00, 0x00, 0x00, 0x05, 0xf5, 0xe1, 0x00, 0x00, 0x00, 0x00, 0x01, 0x86, 0xa0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0a, 0xff, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0x80, 0x20, 0x01, 0x0d, 0xb8, 0x44, 0x73, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff};
                static const uint8_t external_legacy6[] = {0x06, 0x03, 0x00, 0x71, 0x00, 0x00, 0x00, 0x02, 0x0a, 0xff, 0x00, 0x01, 0x0e, 0x00, 0xff, 0x01, 0x00, 0x05, 0xdc, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0a, 0x00, 0x00, 0x00, 0x00, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00, 0x07, 0x0d, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0a, 0xff, 0x00, 0x01, 0x00, 0x00, 0x00, 0x07, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x0a, 0xff, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x03, 0x00, 0x80, 0x20, 0x01, 0x0d, 0xb8, 0x44, 0x73, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff};
                {
                    struct captured_external {
                        const uint8_t *bytes;
                        size_t length;
                        int family;
                        uint32_t tag;
                        uint8_t prefix_length;
                    } cases[] = {
                        {external_csr4, sizeof(external_csr4), AF_INET, 7, 32},
                        {external_csr6, sizeof(external_csr6), AF_INET6, 7, 128},
                    };
                    for (size_t i = 0; i < sizeof(cases)/sizeof(cases[0]); ++i) {
                        uint8_t malformed[sizeof(external_legacy6) + 1];
                        const struct captured_external *c = &cases[i];
                        eigrp_stream_t *frame = eigrp_stream_create(c->length);
                        if (c->family == AF_INET) {
                            eigrp_ipv4_init(&eigrp.af_vectors);
                            neighbor.src.afi = AF_INET;
                            assert(inet_pton(AF_INET, "10.44.53.2",
                                             &neighbor.src.ip.v4) == 1);
                        } else {
                            eigrp_ipv6_init(&eigrp.af_vectors);
                            neighbor.src.afi = AF_INET6;
                            assert(inet_pton(AF_INET6, "fe80::2",
                                             &neighbor.src.ip.v6) == 1);
                        }
                        assert(frame != NULL);
                        assert(eigrp_stream_put(frame, c->bytes, c->length) == c->length);
                        decoded = codec.decoder(&eigrp, &neighbor, frame, c->length);
                        assert(decoded != NULL);
                        assert(decoded->dest.prefix_length == c->prefix_length);
                        assert(decoded->extdata.tag == c->tag);
                        assert(decoded->extdata.protocol == 3);
                        assert(decoded->extdata.reserved == 0);
                        assert(decoded->metric.delay != EIGRP_MAX_METRIC);
                        eigrp_stream_free(frame);

                        /* A wide-metric withdrawal has inaccessible delay,
                         * but retains the same destination/provenance.
                         */
                        memcpy(malformed, c->bytes, c->length);
                        memset(malformed + 20, 0xff, 6);
                        frame = eigrp_stream_create(c->length);
                        assert(eigrp_stream_put(frame, malformed, c->length) == c->length);
                        decoded = codec.decoder(&eigrp, &neighbor, frame, c->length);
                        assert(decoded != NULL);
                        assert(decoded->dest.prefix_length == c->prefix_length);
                        assert(decoded->extdata.tag == c->tag);
                        assert(decoded->metric.delay == EIGRP_MAX_METRIC);
                        eigrp_stream_free(frame);

                        /* All these malformed frames must retire their
                         * temporary route and consume the bounded packet.
                         */
                        for (size_t trim = 1; trim <= 4; trim++) {
                            size_t length = c->length - trim;
                            memcpy(malformed, c->bytes, length);
                            malformed[2] = (uint8_t)(length >> 8);
                            malformed[3] = (uint8_t)length;
                            reject_bytes(&eigrp, &neighbor, &codec, malformed, length);
                        }
                        memcpy(malformed, c->bytes, c->length);
                        malformed[2] = 0;
                        malformed[3] = (uint8_t)(c->length + 1);
                        reject_bytes(&eigrp, &neighbor, &codec, malformed, c->length);
                        memcpy(malformed, c->bytes, c->length);
                        malformed[2] = 0;
                        malformed[3] = (uint8_t)(c->length - 1);
                        reject_bytes(&eigrp, &neighbor, &codec, malformed, c->length);
                        memcpy(malformed, c->bytes, c->length);
                        malformed[12] = 0xff; /* impossible attr words */
                        reject_bytes(&eigrp, &neighbor, &codec, malformed, c->length);
                        memcpy(malformed, c->bytes, c->length);
                        malformed[c->length - (c->prefix_length == 128 ? 17 : 5)] =
                            c->prefix_length == 128 ? 129 : 33;
                        reject_bytes(&eigrp, &neighbor, &codec, malformed, c->length);
                    }
                }

                /* AddPath is deferred. A well-framed additional-path
                 * attribute must not overwrite the primary next hop or
                 * Cisco exterior provenance for either address family.
                 */
                {
                    struct attribute_case {
                        const uint8_t *bytes;
                        size_t length;
                        int family;
                    } cases[] = {
                        {external_csr4, sizeof(external_csr4), AF_INET},
                        {external_csr6, sizeof(external_csr6), AF_INET6},
                    };
                    for (size_t i = 0; i < sizeof(cases)/sizeof(cases[0]); i++) {
                        const struct attribute_case *c = &cases[i];
                        size_t attr_size = c->family == AF_INET ? 16 : 28;
                        size_t insert_at = 36 + (size_t)c->bytes[12] * 2U;
                        size_t total = c->length + attr_size;
                        eigrp_extdata_t saved_extdata;
                        eigrp_addr_t saved_nexthop;
                        eigrp_stream_t *frame;
                        if (c->family == AF_INET) {
                            eigrp_ipv4_init(&eigrp.af_vectors);
                            neighbor.src.afi = AF_INET;
                            assert(inet_pton(AF_INET, "10.44.53.2",
                                             &neighbor.src.ip.v4) == 1);
                        } else {
                            eigrp_ipv6_init(&eigrp.af_vectors);
                            neighbor.src.afi = AF_INET6;
                            assert(inet_pton(AF_INET6, "fe80::2",
                                             &neighbor.src.ip.v6) == 1);
                        }
                        frame = eigrp_stream_create(total);
                        assert(frame != NULL);
                        assert(eigrp_stream_put(frame, c->bytes, c->length) == c->length);
                        decoded = codec.decoder(&eigrp, &neighbor, frame, c->length);
                        assert(decoded != NULL);
                        saved_extdata = decoded->extdata;
                        saved_nexthop = decoded->nexthop;
                        eigrp_stream_set_getp(frame, 0);
                        eigrp_stream_set_endp(frame, total);
                        memmove(frame->data + insert_at + attr_size,
                                frame->data + insert_at, c->length - insert_at);
                        memset(frame->data + insert_at + 2, 0x55, attr_size - 2);
                        frame->data[insert_at] = 7; /* AddPath */
                        frame->data[insert_at + 1] = (uint8_t)((attr_size - 2) / 2);
                        frame->data[12] += (uint8_t)(attr_size / 2);
                        frame->data[2] = (uint8_t)(total >> 8);
                        frame->data[3] = (uint8_t)total;
                        decoded = codec.decoder(&eigrp, &neighbor, frame, total);
                        assert(decoded != NULL);
                        assert(memcmp(&decoded->extdata, &saved_extdata,
                                      sizeof(saved_extdata)) == 0);
                        assert(memcmp(&decoded->nexthop, &saved_nexthop,
                                      sizeof(saved_nexthop)) == 0);
                        eigrp_stream_set_getp(frame, 0);
                        frame->data[insert_at + 1] = 0xff; /* excessive attribute length */
                        assert(codec.decoder(&eigrp, &neighbor, frame, total) == NULL);
                        assert(frame->getp == frame->endp);
                        eigrp_stream_free(frame);
                    }
                }

                /* Legacy 16-byte exterior is not accepted in either AF. */
                {
                    struct legacy_case {
                        const uint8_t *bytes;
                        size_t length;
                        int family;
                    } legacy[] = {
                        {external_legacy4, sizeof(external_legacy4), AF_INET},
                        {external_legacy6, sizeof(external_legacy6), AF_INET6},
                    };
                    for (size_t i = 0; i < sizeof(legacy)/sizeof(legacy[0]); i++) {
                        if (legacy[i].family == AF_INET) {
                            eigrp_ipv4_init(&eigrp.af_vectors);
                        } else {
                            eigrp_ipv6_init(&eigrp.af_vectors);
                        }
                        reject_bytes(&eigrp, &neighbor, &codec,
                                     legacy[i].bytes, legacy[i].length);
                    }
                }

                return 0;
            }
            '''
        )
    )

    compiler = os.environ.get("CC", "cc")
    result = subprocess.run(
        [
            compiler,
            "-std=c11",
            "-D_POSIX_C_SOURCE=200809L",
            "-ffunction-sections",
            "-fdata-sections",
            f"-I{ROOT}",
            str(source),
            str(ROOT / "eigrp" / "code" / "eigrp_tlv1.c"),
            str(ROOT / "eigrp" / "code" / "eigrp_tlv2.c"),
            str(ROOT / "eigrp" / "code" / "eigrp_packet.c"),
            str(ROOT / "eigrp" / "code" / "eigrp_ipv6.c"),
            str(ROOT / "eigrp" / "code" / "eigrp_ipv4.c"),
            str(ROOT / "eigrp" / "code" / "eigrp_stream.c"),
            str(ROOT / "eigrp" / "code" / "eigrp_prefix.c"),
            *( ["-Wl,-dead_strip"] if sys.platform == "darwin" else ["-Wl,--gc-sections"] ),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr

    result = subprocess.run(
        [str(binary)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, result.stderr

    # Decode wire TLVs emitted by the real C codec, not fabricated Python layouts.
    import ipaddress
    sys.path.insert(0,str(ROOT / "unix" / "test"))
    try:
        from eigrp_wire import _route_prefixes
        vectors=[row.split("|",3) for row in result.stdout.splitlines() if row.startswith("VECTOR|")]
        assert len(vectors)==20
        for _, kind, length, hex_bytes in vectors:
            expected=str(ipaddress.ip_network(
                f"2001:db8:1234:5678:9abc:def0:1234:5678/{int(length)}",strict=False))
            assert _route_prefixes(bytes(20)+bytes.fromhex(hex_bytes),6)==[expected],(kind,length)
    finally:
        sys.path.remove(str(ROOT / "unix" / "test"))



def test_v2_is_default_and_ipv6_binds_multiprotocol_afi():
    instance = (ROOT / "eigrp" / "code" / "eigrp_instance.c").read_text()
    metric = (ROOT / "eigrp" / "code" / "eigrp_metric.c").read_text()
    ipv6 = (ROOT / "eigrp" / "code" / "eigrp_ipv6.c").read_text()

    assert "eigrp->metric_version = EIGRP_MAJOR_VERSION;" in instance
    assert "#define EIGRP_MAJOR_VERSION 2" in (ROOT / "eigrp" / "code" / "eigrp_metric.h").read_text()
    assert "context->runtime->metric_version = EIGRP_MAJOR_VERSION;" in metric
    assert "vectors->multiprotocol_afi = EIGRP_AF_IPv6;" in ipv6
