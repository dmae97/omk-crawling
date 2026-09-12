#!/usr/bin/env python3
"""Quick test of the FreshnessEngine self-healing proxy pool."""

import asyncio
import logging

from omk_crawl.tools.freshness_engine import FreshnessEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


async def main():
    print("═" * 60)
    print("  FreshnessEngine Smoke Test")
    print("═" * 60)

    engine = FreshnessEngine()
    await engine.start(initial_harvest=100)

    # Show initial stats
    stats = engine.stats
    print(f"\n📊 Initial: {stats['available']} available, {stats['pool_size']} total")
    print(f"   Fresh harvested: {stats['freshness']['total_harvested']}")

    # Grab 5 proxies
    print("\n📡 Testing proxy rotation...")
    for i in range(5):
        node = engine.next()
        if node:
            print(f"   #{i}: {node.url[:30]}... ({node.protocol}) score={node.score:.1f}")
            engine.mark_success(node, 100)
        else:
            print(f"   #{i}: NO PROXY (pool empty!)")

    # Simulate a dead proxy
    print("\n💀 Simulating dead proxy...")
    node = engine.next()
    if node:
        print(f"   Burning: {node.url[:30]}...")
        engine.mark_dead(node)

    # Check if emergency refill kicked in
    await asyncio.sleep(3)
    stats = engine.stats
    print(f"\n📊 After burn: {stats['available']} available, {stats['pool_size']} total")
    print(f"   Deaths: {stats['freshness']['deaths']}")
    print(f"   Emergencies: {stats['freshness']['emergencies']}")

    # Wait for harvest loop to cycle
    print("\n⏳ Waiting for background harvest cycle (15s)...")
    await asyncio.sleep(15)

    stats = engine.stats
    print(f"\n📊 After cycle: {stats['available']} available, {stats['pool_size']} total")
    print(f"   Fresh harvested: {stats['freshness']['total_harvested']}")
    print(f"   Harvests/min: {stats['freshness']['harvests_per_min']:.1f}")

    await engine.stop()
    print("\n✅ Smoke test complete.")


if __name__ == "__main__":
    asyncio.run(main())
