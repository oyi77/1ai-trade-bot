from datetime import datetime

class StockityBot:
    def __init__(self):
        self.current_time = datetime.now()

    def check_time(self):
        if self.current_time.hour >= 9 and self.current_time.minute >= 30:
            return True
        else:
            return False