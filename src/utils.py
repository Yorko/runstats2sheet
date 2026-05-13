def decimal_pace_to_mmss(pace_decimal: float) -> str:
    """Convert decimal pace (e.g. 5.37) to mm:ss string format (e.g. 5:22)."""
    if not pace_decimal or pace_decimal == 0:
        return "0:00"
    
    minutes = int(pace_decimal)
    seconds = round((pace_decimal - minutes) * 60)
    
    # Handle case where seconds round up to 60
    if seconds == 60:
        minutes += 1
        seconds = 0
        
    return f"{minutes}:{seconds:02d}"
