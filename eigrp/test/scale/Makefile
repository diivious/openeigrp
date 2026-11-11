# Portable route-volume, fault-stress and authenticated tests.
# Copyright (C) 2026 Donnie V. Savage
PYTHON ?= ../../../tools/python
LOG_TEST := ../../../tools/test-log.py

.PHONY: all clean distclean help build peers internal external line kite peer-routes \
	withdraw mcast linkdown mixed-tlv churn auth-md5

all: peers internal external line kite peer-routes withdraw mcast linkdown mixed-tlv churn auth-md5

help:
	@printf '%s\n' 'Use make -C eigrp/test/scale <target> for an individual test:' \
	  '  peers        2,048-neighbor adjacency qualification' \
	  '  internal     100K internal routes into R1' \
	  '  external     100K external routes into R1' \
	  '  line         100K mixed routes over R1-R2-R3' \
	  '  kite         100K mixed routes via three-router kite' \
	  '  peer-routes  2,048 peers each advertising 50 routes' \
	  '  withdraw     Add, withdraw and reinject 100K host routes' \
	  '  mcast        Delayed-ACK multicast pacing and recovery' \
	  '  linkdown     Fail/recover R1-R2 during route injection' \
	  '  mixed-tlv    TLV1/TLV2 peers on the same interface' \
	  '  churn        Repeated route changes, deletes and reinjection' \
	  '  auth-md5     Authenticated 100K-route redistribution'

build:
	$(MAKE) -C ../../.. portable-build
	$(MAKE) -C ../uut eigrp-uut-node

peers: build
	$(PYTHON) $(LOG_TEST) uut/scale -- $(PYTHON) neighbor_2048.py

internal external line kite peer-routes: build
	$(PYTHON) $(LOG_TEST) uut/scale -- $(PYTHON) routes_100k.py $(if $(filter $@,internal),1,$(if $(filter $@,external),2,$(if $(filter $@,line),3,$(if $(filter $@,kite),4,5))))

withdraw mcast linkdown mixed-tlv churn auth-md5: build
	$(PYTHON) $(LOG_TEST) uut/scale -- $(PYTHON) stress_extended.py $@

clean:
	@:

distclean: clean
	rm -rf ../../../logs/uut/scale
