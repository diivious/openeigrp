/*
 * EIGRP Packet Processor for EIGRP TLV2
 * Copyright (C) 2018, 2026
 * Authors:
 *   Donnie Savage
 */
#include <arpa/inet.h>
#include <assert.h>
#include <string.h>

#include "eigrp.h"
#include "eigrp_log.h"
#include "eigrp_types.h"
#include "eigrp_structs.h"

#include "eigrp_interface.h"
#include "eigrp_neighbor.h"
#include "eigrp_packet.h"
#include "eigrp_tlv2.h"
#include "eigrp_topology.h"
#include "eigrp_fsm.h"
#include "eigrp_filter.h"
#include "eigrp_metric.h"
#include "eigrp_network.h"
#include "eigrp_debug.h"

/* Cisco IOS XE multiprotocol wire header: TID, AFI, then RID.
 * RFC 7868 section 6.9.1 shows AFI before TID, but Cisco CSR1000v 17.03.06
 * emits TID before AFI (verified in IPv4/IPv6 protocol-88 captures).
 */
#define EIGRP_TLV2_HEADER_EXT_SIZE 8

/* Wide metric section without extended attributes. */
#define EIGRP_TLV2_METRIC_SIZE 24
#define EIGRP_TLV2_DEST_PREFIX_SIZE 1
#define EIGRP_TLV2_EXTDATA_SIZE 20
#define EIGRP_TLV2_INACCESSIBLE 0xffffffffffffULL
#define EIGRP_TLV2_EXTATTR_HEADER_SIZE 2

/* eigrp_instance::router_id is stored as struct in_addr (network-order
 * bytes), while eigrp_stream_putl() accepts a host-order integer.
 */
static uint32_t eigrp_tlv2_router_id_host(const eigrp_instance_t *eigrp)
{
	return ntohl(eigrp->router_id.s_addr);
}

#define EIGRP_TLV2_INT_MIN_TLV                                                 \
	(EIGRP_TLV_HDR_SIZE + EIGRP_TLV2_HEADER_EXT_SIZE                       \
	 + EIGRP_TLV2_METRIC_SIZE + EIGRP_TLV2_DEST_PREFIX_SIZE)
#define EIGRP_TLV2_EXT_MIN_TLV                                                 \
	(EIGRP_TLV_HDR_SIZE + EIGRP_TLV2_HEADER_EXT_SIZE                       \
	 + EIGRP_TLV2_METRIC_SIZE + EIGRP_TLV2_EXTDATA_SIZE                    \
	 + EIGRP_TLV2_DEST_PREFIX_SIZE)

static size_t eigrp_tlv2_stream_remaining(eigrp_stream_t *pkt)
{
	if (pkt->endp <= pkt->getp)
		return 0;

	return pkt->endp - pkt->getp;
}

static bool eigrp_tlv2_stream_has(eigrp_stream_t *pkt, size_t needed)
{
	return eigrp_tlv2_stream_remaining(pkt) >= needed;
}


static void eigrp_tlv2_decode_abort(eigrp_stream_t *pkt)
{
	eigrp_stream_set_getp(pkt, eigrp_stream_get_endp(pkt));
}

static void eigrp_tlv2_decode_skip(eigrp_stream_t *pkt, size_t tlv_end)
{
	if (tlv_end <= eigrp_stream_get_endp(pkt))
		eigrp_stream_set_getp(pkt, tlv_end);
	else
		eigrp_tlv2_decode_abort(pkt);
}

static uint64_t eigrp_tlv2_get48(eigrp_stream_t *pkt)
{
	uint64_t value = 0;

	for (uint8_t i = 0; i < 6; i++)
		value = (value << 8) | eigrp_stream_getc(pkt);

	return value;
}

static void eigrp_tlv2_put48(eigrp_stream_t *pkt, uint64_t value)
{
	for (int shift = 40; shift >= 0; shift -= 8)
		eigrp_stream_putc(pkt, (value >> shift) & 0xff);
}

static bool eigrp_tlv2_addr_is_zero(const eigrp_instance_t *eigrp,
				    const eigrp_addr_t *address)
{
	static const uint8_t zero[16];
	uint16_t address_bytes;

	if (!eigrp || !address)
		return true;
	address_bytes = eigrp->af_vectors.packet_address_bytes;
	if (!address_bytes || address_bytes > sizeof(zero))
		return true;
	return memcmp(&address->ip, zero, address_bytes) == 0;
}

/* Until AddPath has an independent path lifecycle, treat extended metric
 * attributes as opaque.  Validate the framing, but do not apply additional
 * path data to the primary route descriptor.
 */
static uint16_t eigrp_tlv2_extended_decode(eigrp_stream_t *pkt,
					   uint16_t attr_len)
{
	size_t attr_end;

	if (!attr_len)
		return 0;
	if (!eigrp_tlv2_stream_has(pkt, attr_len))
		return UINT16_MAX;

	attr_end = eigrp_stream_get_getp(pkt) + attr_len;
	while (eigrp_stream_get_getp(pkt) < attr_end) {
		uint8_t words;
		uint16_t data_len;

		if (attr_end - eigrp_stream_get_getp(pkt)
		    < EIGRP_TLV2_EXTATTR_HEADER_SIZE)
			return UINT16_MAX;
		(void)eigrp_stream_getc(pkt); /* attribute opcode */
		words = eigrp_stream_getc(pkt);
		data_len = (uint16_t)words * 2U;
		if (data_len > attr_end - eigrp_stream_get_getp(pkt))
			return UINT16_MAX;
		eigrp_stream_set_getp(pkt,
				      eigrp_stream_get_getp(pkt) + data_len);
	}

	return attr_len;
}

static uint16_t eigrp_tlv2_metric_decode(eigrp_stream_t *pkt,
					 eigrp_route_descriptor_t *route)
{
	eigrp_metrics_t *metric = &route->metric;
	uint8_t attr_words;
	uint16_t attr_len;
	uint16_t reserved;
	uint16_t opaque;
	uint16_t decoded;

	if (!eigrp_tlv2_stream_has(pkt, EIGRP_TLV2_METRIC_SIZE))
		return 0;

	attr_words = eigrp_stream_getc(pkt);
	metric->tag = eigrp_stream_getc(pkt);
	metric->reliability = eigrp_stream_getc(pkt);
	metric->load = eigrp_stream_getc(pkt);
	metric->mtu[2] = eigrp_stream_getc(pkt);
	metric->mtu[1] = eigrp_stream_getc(pkt);
	metric->mtu[0] = eigrp_stream_getc(pkt);
	metric->hop_count = eigrp_stream_getc(pkt);
	metric->delay = eigrp_tlv2_get48(pkt);
	metric->bandwidth = eigrp_tlv2_get48(pkt);
	reserved = eigrp_stream_getw(pkt);
	opaque = eigrp_stream_getw(pkt);
	metric->flags = opaque & 0xff;

	(void)reserved;

	if (metric->delay == EIGRP_TLV2_INACCESSIBLE)
		metric->delay = EIGRP_MAX_METRIC;
	if (metric->bandwidth == EIGRP_TLV2_INACCESSIBLE)
		metric->bandwidth = EIGRP_MAX_METRIC;

	attr_len = (uint16_t)attr_words * 2U;
	decoded = eigrp_tlv2_extended_decode(pkt, attr_len);
	if (decoded == UINT16_MAX)
		return 0;

	return EIGRP_TLV2_METRIC_SIZE + decoded;
}

/* Cisco IOS XE encodes the 32-bit administrative tag between AS and
 * protocol metric in the multiprotocol exterior for both address families.
 * An AddPath encoder is intentionally absent until multi-path ownership and
 * capability negotiation are implemented.
 */
static uint16_t eigrp_tlv2_metric_encode(eigrp_stream_t *pkt,
					 eigrp_route_descriptor_t *route)
{
	eigrp_metrics_t *metric = &route->metric;

	eigrp_stream_putc(pkt, 0); /* no optional extended metric attributes */
	eigrp_stream_putc(pkt, metric->tag);
	eigrp_stream_putc(pkt, metric->reliability);
	eigrp_stream_putc(pkt, metric->load);
	eigrp_stream_putc(pkt, metric->mtu[2]);
	eigrp_stream_putc(pkt, metric->mtu[1]);
	eigrp_stream_putc(pkt, metric->mtu[0]);
	eigrp_stream_putc(pkt, metric->hop_count);
	eigrp_tlv2_put48(pkt, metric->delay == EIGRP_MAX_METRIC
				      ? EIGRP_TLV2_INACCESSIBLE
				      : metric->delay);
	eigrp_tlv2_put48(pkt, metric->bandwidth == EIGRP_MAX_METRIC
				      ? EIGRP_TLV2_INACCESSIBLE
				      : metric->bandwidth);
	eigrp_stream_putw(pkt, 0);
	eigrp_stream_putw(pkt, metric->flags);

	return EIGRP_TLV2_METRIC_SIZE;
}

/* Cisco IOS XE tagged multiprotocol exterior; one layout for IPv4 and IPv6. */
static uint16_t eigrp_tlv2_external_decode(eigrp_stream_t *pkt,
					   eigrp_extdata_t *extdata)
{
	if (!eigrp_tlv2_stream_has(pkt, EIGRP_TLV2_EXTDATA_SIZE))
		return 0;

	extdata->orig = eigrp_stream_getl(pkt);
	extdata->as = eigrp_stream_getl(pkt);
	extdata->tag = eigrp_stream_getl(pkt);
	extdata->metric = eigrp_stream_getl(pkt);
	extdata->reserved = eigrp_stream_getw(pkt);
	extdata->protocol = eigrp_stream_getc(pkt);
	extdata->flags = eigrp_stream_getc(pkt);

	return EIGRP_TLV2_EXTDATA_SIZE;
}

static uint16_t
eigrp_tlv2_external_destination_decode(eigrp_instance_t *eigrp,
				       eigrp_stream_t *pkt,
				       eigrp_route_descriptor_t *route)
{
	uint16_t size, prefix;

	size = eigrp_tlv2_external_decode(pkt, &route->extdata);
	if (!size || route->extdata.reserved != 0)
		return 0;

	prefix = eigrp->af_vectors.packet_prefix_decode(pkt, &route->dest);
	if (!prefix || eigrp_stream_get_getp(pkt) != eigrp_stream_get_endp(pkt))
		return 0;

	return size + prefix;
}

static uint16_t eigrp_tlv2_external_encode(eigrp_instance_t *eigrp,
					   eigrp_stream_t *pkt,
					   eigrp_extdata_t *extdata)
{
	uint32_t rid = extdata->orig ? extdata->orig
				     : eigrp_tlv2_router_id_host(eigrp);

	eigrp_stream_putl(pkt, rid);
	eigrp_stream_putl(pkt, extdata->as);
	eigrp_stream_putl(pkt, extdata->tag);
	eigrp_stream_putl(pkt, extdata->metric);
	eigrp_stream_putw(pkt, extdata->reserved);
	eigrp_stream_putc(pkt, extdata->protocol);
	eigrp_stream_putc(pkt, extdata->flags);

	return EIGRP_TLV2_EXTDATA_SIZE;
}

static uint16_t eigrp_tlv2_route_tlv_type(const eigrp_instance_t *eigrp,
					  const eigrp_route_descriptor_t *route)
{
	if (!eigrp || !route)
		return 0;

	if (route->type == EIGRP_TLV_MP_EXT || route->type == EIGRP_EXT
	    || route->type == eigrp->af_vectors.classic_external_tlv_type)
		return EIGRP_TLV_MP_EXT;

	if (route->type == EIGRP_TLV_MP_INT || route->type == EIGRP_INT
	    || route->type == eigrp->af_vectors.classic_internal_tlv_type)
		return EIGRP_TLV_MP_INT;

	return 0;
}

static eigrp_route_descriptor_t *eigrp_tlv2_decoder(eigrp_instance_t *eigrp,
						    eigrp_nbr_t *nbr,
						    eigrp_stream_t *pkt,
						    uint16_t pktlen)
{
	eigrp_route_descriptor_t *route = NULL;
	size_t tlv_start;
	size_t tlv_end;
	size_t packet_end;
	size_t remaining;
	uint16_t type, length;
	uint16_t bytes = EIGRP_TLV_HDR_SIZE;
	uint16_t decoded;
	uint16_t min_length;
	uint16_t afi;
	uint16_t tid;
	uint32_t rid;
	bool external;

	(void)pktlen;

	if (!eigrp || !nbr || !pkt)
		return NULL;

	tlv_start = eigrp_stream_get_getp(pkt);
	remaining = eigrp_tlv2_stream_remaining(pkt);
	if (remaining < EIGRP_TLV_HDR_SIZE) {
		eigrp_tlv2_decode_abort(pkt);
		return NULL;
	}

	type = eigrp_stream_getw(pkt);
	length = eigrp_stream_getw(pkt);

	if (type == EIGRP_TLV_MP_INT) {
		external = false;
		min_length = EIGRP_TLV2_INT_MIN_TLV;
	} else if (type == EIGRP_TLV_MP_EXT) {
		external = true;
		min_length = EIGRP_TLV2_EXT_MIN_TLV;
	} else {
		if (length >= EIGRP_TLV_HDR_SIZE && length <= remaining)
			eigrp_tlv2_decode_skip(pkt, tlv_start + length);
		else
			eigrp_tlv2_decode_abort(pkt);
		return NULL;
	}

	if (length < min_length || length > remaining) {
		if (eigrp_debug_packet_any_enabled(EIGRP_DEBUG_RECV)) {
			eigrp_log(
				EIGRP_LOG_DEBUG,
				"EIGRP TLV2: Neighbor(%s) corrupt packet type=%u length=%u remaining=%zu",
				eigrp_print_addr(&nbr->src), type, length,
				remaining);
		}
		eigrp_tlv2_decode_abort(pkt);
		return NULL;
	}

	tlv_end = tlv_start + length;
	packet_end = eigrp_stream_get_endp(pkt);
	eigrp_stream_set_endp(pkt, tlv_end);

	if (!eigrp_tlv2_stream_has(pkt, EIGRP_TLV2_HEADER_EXT_SIZE))
		goto malformed;

	tid = eigrp_stream_getw(pkt);
	afi = eigrp_stream_getw(pkt);
	rid = eigrp_stream_getl(pkt);
	bytes += EIGRP_TLV2_HEADER_EXT_SIZE;

	if (afi != eigrp->af_vectors.multiprotocol_afi) {
		eigrp_stream_set_endp(pkt, packet_end);
		eigrp_tlv2_decode_skip(pkt, tlv_end);
		return NULL;
	}

	route = eigrp_topology_route_create(nbr->ei);
	if (!route) {
		eigrp_stream_set_endp(pkt, packet_end);
		eigrp_tlv2_decode_skip(pkt, tlv_end);
		return NULL;
	}

	route->topology_id = (eigrp_topology_id_t)tid;
	route->type = external ? eigrp->af_vectors.classic_external_tlv_type
			       : eigrp->af_vectors.classic_internal_tlv_type;
	route->extdata.orig = rid;

	decoded = eigrp_tlv2_metric_decode(pkt, route);
	if (!decoded)
		goto malformed;
	bytes += decoded;

	/* Cisco always carries an address-family-sized next hop after the
	 * metric, even when it is zero (meaning the advertising neighbor).
	 * A missing address is malformed, never a shortened route TLV.
	 */
	decoded = eigrp->af_vectors.packet_address_decode(pkt, &route->nexthop);
	if (!decoded)
		goto malformed;
	bytes += decoded;

	if (external) {
		/* Cisco 20-byte exterior and exactly one destination. */
		decoded = eigrp_tlv2_external_destination_decode(eigrp, pkt,
								 route);
		if (!decoded)
			goto malformed;
		bytes += decoded;
	} else {
		decoded = eigrp->af_vectors.packet_prefix_decode(pkt,
								 &route->dest);
		if (!decoded)
			goto malformed;
		bytes += decoded;
	}

	if (eigrp_tlv2_addr_is_zero(eigrp, &route->nexthop))
		route->nexthop = nbr->src;

	if (bytes != length)
		goto malformed;

	eigrp_stream_set_endp(pkt, packet_end);
	eigrp_tlv2_decode_skip(pkt, tlv_end);
	return route;

malformed:
	if (eigrp_debug_packet_any_enabled(EIGRP_DEBUG_RECV)) {
		eigrp_log(
			EIGRP_LOG_DEBUG,
			"EIGRP TLV2: Neighbor(%s) malformed TLV type=%u length=%u decoded=%u",
			nbr ? eigrp_print_addr(&nbr->src) : "unknown", type,
			length, bytes);
	}
	if (route)
		eigrp_topology_route_free(route);
	eigrp_stream_set_endp(pkt, packet_end);
	eigrp_tlv2_decode_abort(pkt);
	return NULL;
}

static uint16_t eigrp_tlv2_encoder(eigrp_instance_t *eigrp, eigrp_intf_t *ei,
				   eigrp_nbr_t *nbr, eigrp_stream_t *pkt,
				   eigrp_route_descriptor_t *route)
{
	const eigrp_prefix_t *filter_prefix;
	size_t tlv_start;
	size_t tlv_end;
	uint16_t type;
	uint16_t length;
	uint16_t encoded;

	if (!eigrp || !pkt || !route)
		return 0;
	if (!ei && nbr)
		ei = nbr->ei;
	if (!ei)
		return 0;

	filter_prefix =
		route->prefix ? &route->prefix->destination : &route->dest;
	if (filter_prefix
	    && eigrp_filter_prefix_update(eigrp, ei, EIGRP_FILTER_OUT,
					  filter_prefix)) {
		eigrp_log(
			EIGRP_LOG_INFO,
			"Prefix Filtered:  Setting Metric to EIGRP_MAX_METRIC");
		route->metric.delay = EIGRP_MAX_METRIC;
	}

	type = eigrp_tlv2_route_tlv_type(eigrp, route);
	if (!type)
		return 0;

	tlv_start = eigrp_stream_get_endp(pkt);
	eigrp_stream_putw(pkt, type);
	eigrp_stream_putw(pkt, 0);
	eigrp_stream_putw(pkt, route->topology_id);
	eigrp_stream_putw(pkt, eigrp->af_vectors.multiprotocol_afi);
	eigrp_stream_putl(pkt, eigrp_tlv2_router_id_host(eigrp));

	encoded = eigrp_tlv2_metric_encode(pkt, route);
	if (!encoded) {
		eigrp_stream_set_endp(pkt, tlv_start);
		return 0;
	}

	if (!eigrp->af_vectors.packet_address_encode(pkt, &route->nexthop)) {
		eigrp_stream_set_endp(pkt, tlv_start);
		return 0;
	}

	if (type == EIGRP_TLV_MP_EXT)
		eigrp_tlv2_external_encode(eigrp, pkt, &route->extdata);

	encoded = eigrp->af_vectors.packet_prefix_encode(
		pkt,
		route->prefix ? &route->prefix->destination : &route->dest);
	if (!encoded) {
		eigrp_stream_set_endp(pkt, tlv_start);
		return 0;
	}

	tlv_end = eigrp_stream_get_endp(pkt);
	length = tlv_end - tlv_start;

	eigrp_stream_set_endp(pkt, tlv_start + 2);
	eigrp_stream_putw(pkt, length);
	eigrp_stream_set_endp(pkt, tlv_end);

	return length;
}

void eigrp_tlv2_init(eigrp_tlv_codec_t *codec)
{
	assert(codec);

	codec->decoder = eigrp_tlv2_decoder;
	codec->encoder = eigrp_tlv2_encoder;
}
