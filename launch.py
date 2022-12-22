#!/usr/bin/env python3
import os
import subprocess
from gajim import gajim

import tkinter as tk
import keyring
import random

# Colors
WHITE = "#FFFFFF"
OFF_WHITE = "#F8FAFF"

LABEL_COLOR = "#25265E"

LIGHT_BLUE = "#CCEDFF"
LIGHT_GRAY = "#F5F5F5"

# Fonts
DEFAULT_FONT_STYLE = ("Arial", 20)

DIGITS_FONT_STYLE = ("Arial", 24, "bold")

LARGE_FONT_STYLE = ("Arial", 40, "bold")
SMALL_FONT_STYLE = ("Arial", 16)

class TkinterCalculator:

	def __init__(self):
		self.window = tk.Tk()
		self.window.geometry("375x667")
		self.window.resizable(0, 0)
		self.window.title("Calculator")

		self.vault = None
		self.incorrect_times = 0
		self.recovery_key = None

		self.total_expression = ""
		self.current_expression = ""

		self.display_frame = self.create_display_frame()
		self.total_label, self.label = self.create_display_labels()

		self.digits = {
			7: (1,1),
			8: (1,2),
			9: (1,3),

			4: (2,1),
			5: (2,2),
			6: (2,3),

			1: (3,1),
			2: (3,2),
			3: (3,3),

			0: (4, 2),
			'.':(4, 1)
		}
		self.operations = {
			"/": "\u00F7",
			"*": "\u00D7",
			"-": "-",
			"+": "+",
		}


		self.buttons_frame = self.create_buttons_frame()

		self.buttons_frame.rowconfigure(0, weight=1)
		for x in range(1, 5):
			self.buttons_frame.rowconfigure(x, weight=1)
			self.buttons_frame.columnconfigure(x, weight=1)

		self.create_digit_buttons()
		self.create_operator_buttons()
		self.create_special_buttons()
		self.bind_keys()

	def run(self):
		self.window.mainloop()
	
	# simply func which returns recovery key
	def get_recovery_key(self):
		self.recovery_key = keyring.get_password("sys", "recovery")

		return self.recovery_key
	
	# create recovery key, turn it in str and set it in keyring
	def set_recovery_key(self):

		# gen recov key
		self.recovery_key = str(random.uniform(10, 20))
		
		# cut the key to a valid lenght			
		self.recovery_key = self.recovery_key[:8]

		# set the key in keyring
		keyring.set_password("sys", "recovery", self.recovery_key)		

	# function which raises window with key
	def check_key(self):

		# set the main window
		window = tk.Toplevel()
		window.title("Calculator")
		window.geometry("200x200")

		 
		# Create text widget and specify size
		T = tk.Text(window, height = 5, width = 25)
		 
		# Create label
		l = tk.Label(window, text = "Ur RECOVERY key\n")
		l.config(font =("Courier", 14))
		
		# get the rec key
		Fact = self.get_recovery_key()
		 
		# Create an Ok button.
		b1 = tk.Button(window, text = "OK",
			    command = window.destroy)
		 
		l.pack()
		T.pack()
		b1.pack()
		 
		# Insert The key and display it to user
		T.insert("1.0", Fact)
		 
		# LAUNCH
		tk.mainloop()


	# Create
	def create_sqrt_button(self):
		button = tk.Button(self.buttons_frame, text="\u221ax", bg=OFF_WHITE, fg=LABEL_COLOR, font=DEFAULT_FONT_STYLE, borderwidth=0, command=self.sqrt)
		button.grid(row=0, column=3, sticky=tk.NSEW)

	def create_equals_button(self):
		button = tk.Button(self.buttons_frame, text="=", bg=LIGHT_BLUE, fg=LABEL_COLOR, font=DEFAULT_FONT_STYLE, borderwidth=0, command=self.evaluate)
		button.grid(row=4, column=3, columnspan=2, sticky=tk.NSEW)

	def create_buttons_frame(self):
		frame = tk.Frame(self.window)
		frame.pack(expand=True, fill="both")
		return frame

	def create_special_buttons(self):
		self.create_clear_button()
		self.create_equals_button()
		self.create_square_button()
		self.create_sqrt_button()

	def create_display_labels(self):
		total_label = tk.Label(self.display_frame, text=self.total_expression, anchor=tk.E, bg=LIGHT_GRAY, fg=LABEL_COLOR, padx=24, font=SMALL_FONT_STYLE)
		total_label.pack(expand=True, fill='both')

		label = tk.Label(self.display_frame, text=self.current_expression, anchor=tk.E, bg=LIGHT_GRAY, fg=LABEL_COLOR, padx=24, font=LARGE_FONT_STYLE)
		label.pack(expand=True, fill='both')

		return total_label, label

	def create_display_frame(self):
		frame = tk.Frame(self.window, height=221, bg=LIGHT_GRAY)
		frame.pack(expand=True, fill="both")
		return frame

	def create_digit_buttons(self):
		for digit, grid_value in self.digits.items():
			button = tk.Button(self.buttons_frame, text=str(digit), bg=WHITE, fg=LABEL_COLOR, font=DIGITS_FONT_STYLE, borderwidth=0, command=lambda x=digit: self.add_to_expression(x))
			button.grid(row=grid_value[0], column=grid_value[1], sticky=tk.NSEW)

	def create_operator_buttons(self):
		i = 0
		for operator, symbol in self.operations.items():
			button = tk.Button(self.buttons_frame, text=symbol, bg=OFF_WHITE, fg=LABEL_COLOR, font=DEFAULT_FONT_STYLE, borderwidth=0, command=lambda x=operator: self.append_operator(x))
			button.grid(row=i, column=4, sticky=tk.NSEW)
			i += 1

	def create_clear_button(self):
		button = tk.Button(self.buttons_frame, text="C", bg=OFF_WHITE, fg=LABEL_COLOR, font=DEFAULT_FONT_STYLE, borderwidth=0, command=self.clear)
		button.grid(row=0, column=1, sticky=tk.NSEW)

	def create_square_button(self):
		button = tk.Button(self.buttons_frame, text="x\u00b2", bg=OFF_WHITE, fg=LABEL_COLOR, font=DEFAULT_FONT_STYLE, borderwidth=0, command=self.square)
		button.grid(row=0, column=2, sticky=tk.NSEW)

	def add_to_expression(self, value):
		self.current_expression += str(value)
		self.update_label()

	def append_operator(self, operator):
		self.current_expression += operator
		self.total_expression += self.current_expression
		self.current_expression = ""
		self.update_total_label()
		self.update_label()

	# Handle
	def bind_keys(self):
		self.window.bind("<Return>", lambda event: self.evaluate())
		for key in self.digits:
			self.window.bind(str(key), lambda event, digit=key: self.add_to_expression(digit))

		for key in self.operations:
			self.window.bind(key, lambda event, operator=key: self.append_operator(operator))

	def clear(self):
		self.current_expression = ""
		self.total_expression = ""
		self.update_label()
		self.update_total_label()

	def square(self):
		self.current_expression = str(eval(f"{self.current_expression}**2"))
		self.update_label()


	def sqrt(self):
		self.current_expression = str(eval(f"{self.current_expression}**0.5"))
		self.update_label()

	# Update
	def update_total_label(self):
		expression = self.total_expression

		for operator, symbol in self.operations.items():
			expression = expression.replace(operator, f' {symbol} ')

		self.total_label.config(text=expression)

	def update_label(self):
		self.label.config(text=self.current_expression[:11])

	# Evaluate
	def evaluate(self):
		self.total_expression += self.current_expression
		self.update_total_label()
		
		# check if password is set
		self.vault = keyring.get_password("sys", "user")
		
		# check if calc recov is set
		self.recovery_key = keyring.get_password("sys", "recovery")

		f = os.path.exists(".cache")

		# if password is not set:
		if f is False and self.recovery_key is None and self.vault is None:

			# if this is first start, create a hidden file, which wouldnt let raise recovery key hint again
			fp = open('.cache', 'w')
			fp.close()
			
			# set recovery key
			self.set_recovery_key()

			# show rec key
			self.check_key()


		# if user doesnt set password yet, set it
		elif self.vault is None and self.recovery_key is not None:
			keyring.set_password("sys", "user", self.total_expression)
		
		# if user inputs recovery key:
		elif self.vault is not None and self.total_expression == self.recovery_key:

			# wipe detection file
			os.remove(".cache")
			
			# wipe passwords
			keyring.delete_password("sys", "user")
			keyring.delete_password("sys", "recovery")
			
			# default incor times
			self.incorrect_times = 0
		
		# if user miss password less than 3 times:
		elif self.vault is not None and self.incorrect_times <= 2 and self.total_expression != self.vault:
			self.incorrect_times = self.incorrect_times + 1
		
		# if user miss password more than 3 times WIPE DATA
		elif self.total_expression != self.vault and self.incorrect_times > 2:

			#wipe settings
			os.remove(os.path.expanduser('~') + "/.config/gajim/settings.sqlite")
			os.remove(os.path.expanduser('~') + "/.local/share/gajim/logs.db")

			# lock password by setting it to an impossible to input in calc value
			keyring.set_password("sys", "user", "a")

		# if user inputs right password
		elif self.total_expression == self.vault:

			try:
				p = subprocess.Popen('git rev-parse --short=12 HEAD', shell=True,
									 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
				node = p.communicate()[0]
				if node:
					import gajim as g
					g.__version__ += '+' + node.decode('utf-8').strip()
			except Exception:
				pass

			self.window.destroy()
			gajim.main()

		else:
			pass

		try:
			self.current_expression = str(eval(self.total_expression))
			self.total_expression = ""


		except Exception as e:
			self.current_expression = "Error"
		finally:
			self.update_label()


if __name__ == "__main__":
	tkinter_calculator = TkinterCalculator()
	tkinter_calculator.run()
