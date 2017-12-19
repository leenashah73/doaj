class AuthoriseException(Exception):
    """
    Exception to raise if an action is not authorised
    """
    pass

class NoSuchFormContext(Exception):
    """
    Exception to raise if a form context is requested that can't be found
    """
    pass

class ArgumentException(Exception):
    """
    Exception to raise if an expected argument is not present
    """
    pass