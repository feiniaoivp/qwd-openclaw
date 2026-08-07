#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Search for stocks by name or code using AkShare.
"""

import sys
import akshare as ak

def search_by_name(name):
    """
    Search for stocks by name (partial match).
    """
    try:
        # Get all stock info (A-shares)
        stock_info_a_code_name = ak.stock_info_a_code_name()
        # Filter by name containing the keyword
        result = stock_info_a_code_name[stock_info_a_code_name['name'].str.contains(name, na=False)]
        return result
    except Exception as e:
        print(f"Error searching by name: {e}")
        return None

def search_by_code(code):
    """
    Search for stocks by code (exact or partial match).
    """
    try:
        stock_info_a_code_name = ak.stock_info_a_code_name()
        result = stock_info_a_code_name[stock_info_a_code_name['code'].str.contains(code, na=False)]
        return result
    except Exception as e:
        print(f"Error searching by code: {e}")
        return None

def main():
    if len(sys.argv) < 3:
        print("Usage: python search_stocks.py [name|code] <keyword>")
        sys.exit(1)
    
    search_type = sys.argv[1].lower()
    keyword = sys.argv[2]
    
    if search_type == 'name':
        result = search_by_name(keyword)
        print(f"Searching for stocks with name containing '{keyword}':")
    elif search_type == 'code':
        result = search_by_code(keyword)
        print(f"Searching for stocks with code containing '{keyword}':")
    else:
        print("Invalid search type. Use 'name' or 'code'.")
        sys.exit(1)
    
    if result is not None and not result.empty:
        print(result.to_string(index=False))
    else:
        print("No matches found.")

if __name__ == "__main__":
    main()