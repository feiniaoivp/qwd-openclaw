#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import akshare as ak
import pandas as pd

def main():
    try:
        # Get list of all A-shares with code and name
        # Using stock_info_a_code_name provides code and name
        df = ak.stock_info_a_code_name()
        # Ensure columns are present
        # Typically columns: code, name
        # Rename if necessary
        if 'code' not in df.columns or 'name' not in df.columns:
            # If column names differ, try to infer
            # Some versions have 'code' and 'name' in Chinese?
            # Let's print columns for debug
            print("Columns:", df.columns.tolist())
            # fallback: assume first two columns
            df = df.rename(columns={df.columns[0]: 'code', df.columns[1]: 'name'})
        # Keep only code and name
        df = df[['code', 'name']]
        # Ensure name is string
        df['name'] = df['name'].astype(str)
        df['code'] = df['code'].astype(str)

        # Define search targets
        targets = [
            ('富通微电', lambda s: '富通' in s and '微电' in s),
            ('机电B股', lambda s: '机电' in s and 'B' in s),
            ('福莱安特', lambda s: '福莱安特' in s)
        ]

        found_any = False
        for target, condition in targets:
            print(f"\nSearching for '{target}':")
            # Exact match
            exact = df[df['name'] == target]
            if not exact.empty:
                print("Exact match:")
                for _, row in exact.iterrows():
                    print(f"  Code: {row['code']}, Name: {row['name']}")
                found_any = True
                continue  # Skip fuzzy if exact found? We can still do fuzzy, but spec says try fuzzy if not exact.
            # Fuzzy match
            fuzzy = df[df['name'].apply(condition)]
            if not fuzzy.empty:
                print("Fuzzy match:")
                for _, row in fuzzy.iterrows():
                    print(f"  Code: {row['code']}, Name: {row['name']}")
                found_any = True
            else:
                print("  No matches found.")
        if not found_any:
            print("\nNo matches found for any target.")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()