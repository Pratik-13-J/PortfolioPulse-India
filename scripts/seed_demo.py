from portfoliopulse.bootstrap import ensure_demo_data

if __name__ == "__main__":
    rows = ensure_demo_data()
    print(f"India demo portfolio is ready; imported {rows:,} synthetic price observations.")
